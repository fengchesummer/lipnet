# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""
Module: preprocess_data
Reference: https://github.com/rizkiarm/LipNet
"""

# pylint: disable=too-many-locals, no-self-use, c-extension-no-member

import os
import fnmatch
import errno
import numpy as np
from scipy import ndimage
from scipy.misc import imresize
from skimage import io
import skvideo.io
import dlib

def mkdir_p(path):
    """
    Make a diretory
    """
    try:
        os.makedirs(path)
    except OSError as exc:  # Python >2.5
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else:
            raise

def find_files(directory, pattern):
    """
    Find files
    """
    for root, _, files in os.walk(directory):
        for basename in files:
            if fnmatch.fnmatch(basename, pattern):
                filename = os.path.join(root, basename)
                yield filename

class Video(object):
    """
    Preprocess for Video
    """
    def __init__(self, vtype='mouth', face_predictor_path=None):
        if vtype == 'face' and face_predictor_path is None:
            raise AttributeError('Face video need to be accompanied with face predictor')
        self.face_predictor_path = face_predictor_path
        self.vtype = vtype
        self.face = None
        self.mouth = None
        self.data = None
        self.length = None

    def from_frames(self, path):
        """
        Read from frames
        """
        frames_path = sorted([os.path.join(path, x) for x in os.listdir(path)])
        frames = [ndimage.imread(frame_path) for frame_path in frames_path]
        self.handle_type(frames)
        return self

    def from_video(self, path):
        """
        Read from videos
        """
        frames = self.get_video_frames(path)
        self.handle_type(frames)
        return self

    def from_array(self, frames):
        """
        Read from array
        """
        self.handle_type(frames)
        return self

    def handle_type(self, frames):
        """
        Config video types
        """
        if self.vtype == 'mouth':
            self.process_frames_mouth(frames)
        elif self.vtype == 'face':
            self.process_frames_face(frames)
        else:
            raise Exception('Video type not found')

    def process_frames_face(self, frames):
        """
        Preprocess from frames using face detector
        """
        detector = dlib.get_frontal_face_detector()
        predictor = dlib.shape_predictor(self.face_predictor_path)
        mouth_frames = self.get_frames_mouth(detector, predictor, frames)
        self.face = np.array(frames)
        self.mouth = np.array(mouth_frames)
        self.set_data(mouth_frames)

    def process_frames_mouth(self, frames):
        """
        Preprocess from frames using mouth detector
        """
        self.face = np.array(frames)
        self.mouth = np.array(frames)
        self.set_data(frames)

    def get_frames_mouth(self, detector, predictor, frames):
        """
        Get frames using mouth crop
        """
        mouth_width = 100
        mouth_height = 50
        horizontal_pad = 0.19
        normalize_ratio = None
        mouth_frames = []
        for frame in frames:
            dets = detector(frame, 1)
            shape = None
            for det in dets:
                shape = predictor(frame, det)
                i = -1
            if shape is None: # Detector doesn't detect face, just return as is
                return frames
            mouth_points = []
            for part in shape.parts():
                i += 1
                if i < 48: # Only take mouth region
                    continue
                mouth_points.append((part.x, part.y))
            np_mouth_points = np.array(mouth_points)

            mouth_centroid = np.mean(np_mouth_points[:, -2:], axis=0)

            if normalize_ratio is None:
                mouth_left = np.min(np_mouth_points[:, :-1]) * (1.0 - horizontal_pad)
                mouth_right = np.max(np_mouth_points[:, :-1]) * (1.0 + horizontal_pad)

                normalize_ratio = mouth_width / float(mouth_right - mouth_left)

            new_img_shape = (int(frame.shape[0] * normalize_ratio),
                             int(frame.shape[1] * normalize_ratio))
            resized_img = imresize(frame, new_img_shape)

            mouth_centroid_norm = mouth_centroid * normalize_ratio

            mouth_l = int(mouth_centroid_norm[0] - mouth_width / 2)
            mouth_r = int(mouth_centroid_norm[0] + mouth_width / 2)
            mouth_t = int(mouth_centroid_norm[1] - mouth_height / 2)
            mouth_b = int(mouth_centroid_norm[1] + mouth_height / 2)

            mouth_crop_image = resized_img[mouth_t:mouth_b, mouth_l:mouth_r]

            mouth_frames.append(mouth_crop_image)
        return mouth_frames

    def get_video_frames(self, path):
        """
        Get video frames
        """
        videogen = skvideo.io.vreader(path)
        frames = np.array([frame for frame in videogen])
        return frames

    def set_data(self, frames):
        """
        Prepare the input of model
        """
        data_frames = []
        for frame in frames:
            #frame H x W x C
            frame = frame.swapaxes(0, 1) # swap width and height to form format W x H x C
            if len(frame.shape) < 3:
                frame = np.array([frame]).swapaxes(0, 2).swapaxes(0, 1) # Add grayscale channel
            data_frames.append(frame)
        frames_n = len(data_frames)
        data_frames = np.array(data_frames) # T x W x H x C
        data_frames = np.rollaxis(data_frames, 3) # C x T x W x H
        data_frames = data_frames.swapaxes(2, 3) # C x T x H x W  = NCDHW

        self.data = data_frames
        self.length = frames_n

class Align(object):
    """
    Preprocess for Align
    """

    def __init__(self, absolute_max_string_len=32, label_func=None):
        self.label_func = label_func
        self.absolute_max_string_len = absolute_max_string_len
        self.align = None
        self.sentence = None
        self.label = None
        self.padded_label = None

    def from_file(self, path):
        """
        Read from files
        """
        with open(path, 'r') as file:
            lines = file.readlines()
        align = [(int(y[0])/1000, int(y[1])/1000, y[2]) \
                 for y in [x.strip().split(" ") for x in lines]]
        self.build(align)
        return self

    def from_array(self, align):
        """
        Read from array
        """
        self.build(align)
        return self

    def build(self, align):
        """
        Build the align array
        """
        self.align = self.strip(align, ['sp', 'sil'])
        self.sentence = self.get_sentence(align)
        self.label = self.get_label(self.sentence)
        self.padded_label = self.get_padded_label(self.label)

    def strip(self, align, items):
        """
        Strip
        """
        return [sub for sub in align if sub[2] not in items]

    def get_sentence(self, align):
        """
        Get sentence
        """
        return " ".join([y[-1] for y in align if y[-1] not in ['sp', 'sil']])

    def get_label(self, sentence):
        """
        Get label
        """
        return self.label_func(sentence)

    def get_padded_label(self, label):
        """
        Get padded label
        """
        padding = np.ones((self.absolute_max_string_len-len(label))) * -1
        return np.concatenate((np.array(label), padding), axis=0)

    @property
    def word_length(self):
        """
        word length
        """
        return len(self.sentence.split(" "))

    @property
    def sentence_length(self):
        """
        sentence length
        """
        return len(self.sentence)

    @property
    def label_length(self):
        """
        label length
        """
        return len(self.label)

def preprocess(from_idx, to_idx, _params):
    """
    Preprocess: Convert a video into the mouth images
    """
    source_exts = '*.mpg'
    source_path = _params['src_path']
    target_path = _params['tgt_path']
    face_predictor_path = './shape_predictor_68_face_landmarks.dat'

    succ = set()
    fail = set()
    for idx in range(from_idx, to_idx):
        source_path = source_path + '/' + 's' + str(idx) + '/'
        try:
            for filepath in find_files(source_path, source_exts):
                print("Processing: {}".format(filepath))
                video = Video(vtype='face', \
                              face_predictor_path=face_predictor_path).from_video(filepath)

                filepath_wo_ext = os.path.splitext(filepath)[0].split('/')[-1]
                target_dir = os.path.join(target_path, filepath_wo_ext)
                mkdir_p(target_dir)

                i = 0
                for frame in video.mouth:
                    io.imsave(os.path.join(target_dir, "mouth_{0:03d}.png".format(i)), frame)
                    i += 1
            succ.add(idx)
        except OSError as error:
            print(error)
            fail.add(idx)
    return (succ, fail)

if __name__ == '__main__':
    import argparse
    from multi import multi_p_run, put_worker
    PARSER = argparse.ArgumentParser()
    PARSER.add_argument('--src_path', type=str, default='../data/mp4s')
    PARSER.add_argument('--tgt_path', type=str, default='../data/datasets')
    PARSER.add_argument('--align_path', type=str, default='../data/align')
    CONFIG = PARSER.parse_args()
    PARAMS = {'src_path':CONFIG.src_path,
              'tgt_path':CONFIG.tgt_path,
              'align_path':CONFIG.align_path}

    os.makedirs('{tgt_path}'.format(tgt_path=PARAMS['tgt_path']), exist_ok=True)
    os.system('rm -rf {tgt_path}'.format(tgt_path=PARAMS['tgt_path']))
    RES = multi_p_run(35, put_worker, preprocess, PARAMS, 9)
