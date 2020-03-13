import os

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '1'

import platform

if platform.system() == 'Windows':
    os.environ['PATH'] = 'C:\\Users\\zjb\\AppData\\Roaming\\Mathematica\\Paclets\\Repository\\' \
                         'CUDAResources-Win64-12.0.359\\CUDAToolkit\\bin;' + os.environ['PATH']

import tensorflow as tf

N0 = 1.
# RES = (0.315534, 0.315534, 0.236686)  # pixel_size = RES * lambda0
RES = (0.25, 0.25, 0.1)
SIZE = (512, 512, 0)  # better to be even numbers
DATA_TYPE = tf.complex128
EPS = 1E-6
# CIRCULATE_ALLOW = False
# DATA_TYPE_R = tf.float32

# K0 = 2 * math.pi / WAVELENGTH
# N0 = 1
