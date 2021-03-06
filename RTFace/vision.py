#! /usr/bin/env python2

import ioutil
import cv2
import dlib
import base64
import numpy as np
import json
from camShift import camshiftTracker, meanshiftTracker
from demo_config import Config


LOG = ioutil.getLogger(__name__)


def clamp(n, minn, maxn):
    return max(min(maxn, n), minn)

# Tracking
class TrackerInitializer(object):
    def __init__(self, prev_frame, prev_roi, frame):
        self.prev_frame = prev_frame
        self.prev_roi = prev_roi
        self.frame = frame


def create_dlib_tracker(frame, roi):
    tracker = dlib.correlation_tracker()
    (roi_x1, roi_y1, roi_x2, roi_y2) = roi
    tracker.start_track(frame,
                        dlib.rectangle(roi_x1, roi_y1, roi_x2, roi_y2))
    return tracker


@ioutil.timeit
def create_tracker(frame, roi, use_dlib=False):
    if not (isinstance(roi, dlib.rectangle)):
        bx = tuple_to_drectangle(roi)
    else:
        bx = roi

    if use_dlib:
        tracker = dlib.correlation_tracker()
    else:
        tracker = meanshiftTracker()
    tracker.start_track(frame, bx)
    LOG.debug('create tracker received: {}'.format(bx))
    return tracker


def create_trackers(frame, rois, use_dlib=False):
    trackers = []
    for roi in rois:
        if use_dlib:
            tracker = create_tracker(frame, roi, use_dlib=True)
        else:
            tracker = create_tracker(frame, roi)
        trackers.append(tracker)
    return trackers

# dlib wrappers


def drectangle_to_tuple(drectangle):
    if isinstance(drectangle, dlib.rectangle) or isinstance(drectangle, dlib.drectangle):
        cur_roi = (int(drectangle.left()),
                   int(drectangle.top()),
                   int(drectangle.right()),
                   int(drectangle.bottom()))
        return cur_roi
    else:
        return drectangle


def tuple_to_drectangle(bx):
    if isinstance(bx, tuple):
        (roi_x1, roi_y1, roi_x2, roi_y2) = bx
        return dlib.rectangle(roi_x1, roi_y1, roi_x2, roi_y2)
    else:
        return bx

# distance


def euclidean_distance_square(roi1, roi2):
    result = abs(roi1[0] - roi2[0])**2 + abs(roi1[1] - roi2[1])**2
    return result

# np helpers


def np_array_to_jpeg_string(frame):
    # face_img = Image.fromarray(frame)
    # sio = StringIO.StringIO()
    # face_img.save(sio, 'JPEG')
    # jpeg_img = sio.getvalue()
    _, jpeg_img = cv2.imencode('.jpg', frame)
    face_string = base64.b64encode(jpeg_img)
    return face_string


def np_array_to_string(frame):
    frame_bytes = frame.tobytes()
    face_string = base64.b64encode(frame_bytes)
    return face_string


def np_array_to_jpeg_data_url(frame):
    face_string = np_array_to_jpeg_string(frame)
    face_string = "data:image/jpeg;base64," + face_string
    return face_string

# cv2 helpers


def imwrite_rgb(path, frame):
    frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    sys.stdout.write('writing img to {}\n'.format(path))
    sys.stdout.flush()
    cv2.imwrite(path, frame)


def draw_rois(img, rois, hint=None):
    for roi in rois:
        (x1, y1, x2, y2) = tuple(roi)
        if hint:
            cv2.putText(img, hint, (x1, y1),
                        cv2.FONT_HERSHEY_PLAIN, 1, (255, 0, 0))
        cv2.rectangle(img, (x1, y1), (x2, y2), (255, 0, 0))


# face detection
MIN_WIDTH_THRESHOLD = 3
MIN_HEIGHT_THRESHOLD = 3


def is_small_face(roi):
    (x1, y1, x2, y2) = roi
    # has negative number
    if (x1 < 0 or y1 < 0 or x2 < 0 or y2 < 0):
        return True
    # region too small
    if (abs(x2 - x1) < MIN_WIDTH_THRESHOLD or abs(y2 - y1) < MIN_HEIGHT_THRESHOLD):
        LOG.debug('face too small discard')
        return True
    return False


def filter_small_faces(dets):
    filtered_dets = []
    for i, d in enumerate(dets):
        if not is_small_face(d):
            filtered_dets.append(d)
    return filtered_dets


@ioutil.timeit
def detect_faces(frame, detector, largest_only=False, upsample_num_times=0, adjust_threshold=0.0):
    # upsampling will take a lot of time
    # http://dlib.net/face_detector.py.html
    dets, scores, sub_detector_indices = detector.run(
        frame, upsample_num_times, adjust_threshold)
    if largest_only:
        if (len(dets) > 0):
            max_det = max(dets, key=lambda rect: rect.width() * rect.height())
            dets = [max_det]

    dets = map(lambda d: (int(d.left()), int(d.top()),
                          int(d.right()), int(d.bottom())), dets)
    rois = filter_small_faces(dets)
    LOG.debug('# face detected : {}'.format(len(rois)))
    rois = sorted(rois)
    return rois


def is_gray_scale(img):
    if len(img.shape) == 2:
        return True
    else:
        return False


# merge old facerois with new face rois
def merge_faceROIs(old_faceROIs, new_faceROIs):
    pass


def get_image_region(img, drect):
    (x1, y1, x2, y2) = drectangle_to_tuple(drect)
    h, w, _ = img.shape
    x1 = clamp(x1, 0, w - 1)
    y1 = clamp(y1, 0, h - 1)
    x2 = clamp(x2, 0, w - 1)
    y2 = clamp(y2, 0, h - 1)
    return img[y1:y2 + 1, x1:x2 + 1]

# the lower the number is, the higher of blurness


def variance_of_laplacian(bgr_img):
    # compute the Laplacian of the image and then return the focus
    # measure, which is simply the variance of the Laplacian
    if len(bgr_img.shape) == 3:
        grey_img = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2GRAY)
    else:
        grey_img = bgr_img
    return cv2.Laplacian(grey_img, cv2.CV_64F).var()

# detect if an image is blurry
# a higher threshold, meaning a higher demand for image being clearer


def is_clear(bgr_img, threshold=40):
    if variance_of_laplacian(bgr_img) < threshold:
        return False
    return True


class FaceROI(object):
    PROFILE_FACE = 'profile_face'
    # dlib arbitrary number
    TRACKER_CONFIDENCE_THRESHOLD = 2

    def __init__(self, roi, data=None, name=None, tracker=None, frid=-1):
        self.roi = drectangle_to_tuple(roi)
        self.data = data
        self.name = name
        self.tracker = tracker
        self.swap_tmp_data = None
        self.frid = frid
        self.low_confidence = False

    def __copy__(self):
        newone = FaceROI(self.roi,
                         data=None,
                         name=self.name,
                         tracker=None,
                         frid=self.frid)
        return newone

    # returned ROI may go out of bounds --> representing failure of tracking
    def get_json(self, send_data=False):
        (roi_x1, roi_y1, roi_x2, roi_y2) = self.roi
        msg = {
            'roi_x1': roi_x1,
            'roi_y1': roi_y1,
            'roi_x2': roi_x2,
            'roi_y2': roi_y2,
            'name': self.name
        }
        if send_data:
            msg['data'] = np_array_to_jpeg_string(self.data)
        return json.dumps(msg)

    # return the center location of the face
    def get_location(self):
        (roi_x1, roi_y1, roi_x2, roi_y2) = self.roi
        return ((roi_x1 + roi_x2) / 2, (roi_y1 + roi_y2) / 2)

    def __str__(self):
        return 'frid {}: {}, {}'.format(self.frid, self.roi, self.name)

    def __repr__(self):
        return 'frid {}: {}, {}'.format(self.frid, self.roi, self.name)

    def update_tracker_failure(self, conf):
        self.low_confidence = (
            self.name != self.PROFILE_FACE and conf < self.TRACKER_CONFIDENCE_THRESHOLD)
        return self.low_confidence


class FaceFrame(object):
    def __init__(self, fid, frame, faceROIs):
        # frame id
        self.fid = fid
        self.frame = frame
        self.faceROIs = faceROIs

    def __repr__(self):
        return '{}: {}'.format(self.fid, self.faceROIs)

    def __str__(self):
        return '{}: {}'.format(self.fid, self.faceROIs)

    def has_bx(self, bx):
        for faceROI in self.faceROIs:
            if iou_area(faceROI.roi, bx) > 0.5:
                return True
        return False


def enlarge_roi(roi, padding, frame_width, frame_height):
    (x1, y1, x2, y2) = roi
    x1 = max(x1 - padding, 0)
    y1 = max(y1 - padding, 0)
    x2 = min(x2 + padding, frame_width - 1)
    y2 = min(y2 + padding, frame_height - 1)
    return (x1, y1, x2, y2)


def clamp_roi(roi, frame_width, frame_height):
    (x1, y1, x2, y2) = roi
    x1 = clamp(x1, 0, frame_width - 1)
    y1 = clamp(y1, 0, frame_height - 1)
    x2 = clamp(x2, 0, frame_width - 1)
    y2 = clamp(y2, 0, frame_height - 1)
    return (x1, y1, x2, y2)


def iou_area(a, b):
    # compute overlaps
    # intersection
    a = drectangle_to_tuple(a)
    b = drectangle_to_tuple(b)
    ixmin = np.maximum(a[0], b[0])
    iymin = np.maximum(a[1], b[1])
    ixmax = np.minimum(a[2], b[2])
    iymax = np.minimum(a[3], b[3])
    iw = np.maximum(ixmax - ixmin + 1., 0.)
    ih = np.maximum(iymax - iymin + 1., 0.)
    inters = iw * ih

    uni = ((b[2] - b[0] + 1.) * (b[3] - b[1] + 1.) +
           (a[2] - a[0] + 1.) *
           (a[3] - a[1] + 1.) - inters)

    overlaps = clamp(1.0 * inters / uni, 0, 1)
    return overlaps


def enlarge_drectangles(sm_dets, enlarge_ratio):
    if isinstance(sm_dets, dlib.rectangles):
        dets = dlib.rectangles()
        for sm_det in sm_dets:
            dets.append(dlib.rectangle(
                int(sm_det.left() * enlarge_ratio),
                int(sm_det.top() * enlarge_ratio),
                int(sm_det.right() * enlarge_ratio),
                int(sm_det.bottom() * enlarge_ratio),
            ))
        return dets
    elif isinstance(sm_dets, dlib.rectangle):
        det = dlib.rectangle(
            int(sm_det.left() * enlarge_ratio),
            int(sm_det.top() * enlarge_ratio),
            int(sm_det.right() * enlarge_ratio),
            int(sm_det.bottom() * enlarge_ratio))
        return det
    elif isinstance(sm_dets, tuple) and len(sm_dets) == 4:
        return (sm_dets[0] * enlarge_ratio,
                sm_dets[1] * enlarge_ratio,
                sm_dets[2] * enlarge_ratio,
                sm_dets[3] * enlarge_ratio)
    else:
        raise TypeError(
            'sm_dets needs to be type dlib.drectangles or dlib.rectangle. but is {}'.format(type(sm_dets)))


def downsample(rgb_img, shrink_ratio):
    return cv2.resize(rgb_img, None, fx=1.0 / shrink_ratio, fy=1.0 / shrink_ratio)
