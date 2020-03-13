from const import *
import tensorflow as tf
from tensorflow_core import Tensor
import numpy as np


def _c_gamma():
    """
    Calculate cos(gamma) as a constant array at frequency domain. Gamma is the angle
    between the wave vector and z-axis. Note: N.A.=sin(gamma)

    The array is pre-shifted for later FFT operation.

    :return: cos(gamma) numpy array
    """
    c_alpha, c_beta = [
        np.fft.ifftshift(np.arange(-SIZE[i] / 2, SIZE[i] / 2).astype(np.double)) / SIZE[i] / RES[i]
        for i in (0, 1)
    ]
    c_gamma = np.sqrt(np.maximum(1 - (np.square(c_alpha) + np.square(c_beta[:, None])), EPS))
    return c_gamma


def _outflow_absorb() -> np.ndarray:
    """
    Absorb as a shape of **0.1 power** of **(sin(x)+1)/2** for x at 0~Pi/2, which is a
    very light absorption (about 0.93~1) per step to avoid extra diffraction pattern.

    If not absorbed thoroughly for very high N.A., try to reduce RES(z)/RES(x,y)

    :return: a 2D transmittance numpy array
    """

    x, y = [np.minimum(np.arange(SIZE[i]), SIZE[i] - np.arange(SIZE[i])).astype(np.double) / SIZE[i]
            for i in (0, 1)]
    strength = 0.1 * RES[2] / min(RES[:2])
    x, y = map(lambda a: np.power(0.5 + 0.5 * np.sin(np.minimum(a, 0.1) * 5 * np.pi), strength), (x, y))
    return x * y[:, None]


def _evanescent_absorb() -> np.ndarray:
    """
        For 0.98<N.A.<1, decrease to 1~1/e per step

        For N.A.>1 decrease to 1/e per step

        :return: a 2D angular spectrum transmittance numpy array
    """
    return np.exp(np.minimum((_c_gamma() - 0.2) * 5, 0))


@tf.function
def pupil(u, na):
    mask = (_c_gamma() > np.sqrt(1 - na ** 2)).astype(np.double)
    return tf.signal.ifft2d(tf.signal.fft2d(u) * mask)

@tf.function
def _kz():
    kz = tf.constant(_c_gamma() * (2 * np.pi * RES[2] * N0), DATA_TYPE)
    return kz


@tf.function
def nature_d(u: Tensor):
    """
    Calculate z partial derivative for a initial x-y complex amplitude in free
    (or homogeneous) space due to pure forward propagation.

    :param u: x-y complex amplitude
    :return: z partial derivative of u
    """
    a = tf.signal.fft2d(u)
    a_d = _kz() * a * 1j
    u_d = tf.signal.ifft2d(a_d)
    return u_d


@tf.function
def ssnp_step(u: Tensor, u_d: Tensor, dz, n=None) -> tuple:
    """
    SSNP main operation of one step

    :param u: x-y complex amplitude
    :param u_d: z partial derivative of u
    :param dz: step size along z axis
    :param n: refractive index along x-y distribution in this slice. Use background N0 if not provided.
    :return: new (u, u_d) after a step towards +z direction
    """
    for t_in in (u, u_d, n):
        if t_in is not None:
            if t_in.shape != SIZE[:2]:
                raise ValueError(f"the x,y shape {t_in.shape} is not {SIZE[:2]}")

    a = tf.signal.fft2d(u)
    a_d = tf.signal.fft2d(u_d)
    evan_rm = tf.constant(_evanescent_absorb(), DATA_TYPE)
    a *= evan_rm
    a_d *= evan_rm
    u = tf.signal.ifft2d(tf.cos(_kz() * dz) * a + (tf.sin(_kz() * dz) / _kz()) * a_d)
    u_d = tf.signal.ifft2d(-(tf.sin(_kz() * dz) * _kz()) * a + tf.cos(_kz() * dz) * a_d)
    # n = n / N0
    if n is not None:
        u_d -= ((2 * np.pi * RES[2]) ** 2 * dz) * (n * (2 * N0 + n) * u)
    # absorb = tf.constant(_outflow_absorb(), DATA_TYPE)
    # u *= absorb
    # u_d *= absorb
    return u, u_d


@tf.function
def bpm_step(u: Tensor, dz, n=None) -> tuple:
    """
    BPM main operation of one step

    :param u: x-y complex amplitude
    :param dz: step size along z axis
    :param n: refractive index along x-y distribution in this slice. Use background N0 if not provided.
    :return: new (u, u_d) after a step towards +z direction
    """
    for t_in in (u, n):
        if t_in is not None:
            if t_in.shape != SIZE[:2]:
                raise ValueError(f"the x,y shape {t_in.shape} is not {SIZE[:2]}")

    a = tf.signal.fft2d(u)
    evan_rm = tf.constant(_evanescent_absorb(), DATA_TYPE)
    a *= evan_rm
    u = tf.signal.ifft2d(tf.exp(_kz() * (1j * dz)) * a)
    if n is not None:
        u *= tf.exp(n * (1j * (2 * np.pi * RES[2] * N0) * dz))
    absorb = tf.constant(_outflow_absorb(), DATA_TYPE)
    u *= absorb
    return u


@tf.function
def n_ball(n, r, *, z_empty: tuple) -> Tensor:
    """
    :param n: refractive index
    :param r: ball radius (times of wavelength)
    :param z_empty: empty space before and after the ball
    :return: tf constant of the 3d refractive index field
    """
    assert type(z_empty) == tuple and len(z_empty) == 2
    arr = tf.square(tf.cast(tf.range(-SIZE[1] / 2, SIZE[1] / 2), tf.float64) * RES[1])[None, None, :]
    arr += tf.square(tf.cast(tf.range(-SIZE[0] / 2, SIZE[0] / 2), tf.float64) * RES[0])[None, :, None]
    arr += tf.square(tf.cast(tf.range(-z_empty[0] - r / RES[2], z_empty[1] + r / RES[2]), tf.float64) * RES[2])[
           :, None, None]
    out = tf.cast(tf.minimum(tf.exp(-arr / (r * r) * 9.21034), 0.0001) * 10000 * n, DATA_TYPE)
    return out


def backprop_loss(model, re, im):
    @tf.function
    def func():
        u, u_d = model(re, im)
        a = tf.signal.fft2d(u)
        a_d = tf.signal.fft2d(u_d)
        return tf.reduce_sum(tf.abs((a_d - _kz() * a * 1j)))

    return func


def zz_loss(model, re, im, u0):
    @tf.function
    def func():
        u, _ = model(re, im)
        return tf.reduce_sum(tf.abs(u - u0))

    return func


class Beam:
    u = u_d = None
    a = a_d = None
    n = None
    is_free = True
    back_prop = False

    def __init__(self, u, n, free=(0, 0), back_prop=False):
        if u.shape != SIZE[:2]:
            raise ValueError(f"the x,y shape of input field {u.shape} is not {SIZE[:2]}")
        if n.shape[1:] != SIZE[:2]:
            raise ValueError(f"the x,y shape of medium {n.shape} is not {SIZE[:2]}")
        self.u = u
        self.a = tf.signal.fft2d(u)
        self.n = n
        self.free = free
        self.back_prop = back_prop

    def steps(self, method='SSNP', pass_free=True):
        method = method.upper()
        method = {'SSNP': self.ssnp_step, 'BPM': self.bpm_step}[method]
        direction = {False: 1, True: -1}[self.back_prop]
        pre, post = reversed(self.free) if self.back_prop else self.free
        n = reversed(self.n) if self.back_prop else self.n
        if not pass_free:
            for _ in range(pre):
                method(direction)
                yield
        elif pre > 0:
            method(direction * pre)
            yield

        self.is_free = False
        for n_i in n:
            method(direction, n_i)
            yield

        self.is_free = True
        if not pass_free:
            for _ in range(post):
                method(direction)
                yield
        elif post > 0:
            method(direction * post)
            yield

    @tf.function
    def ssnp_step(self, dz, n=None):
        pass

    @tf.function
    def bpm_step(self, dz, n=None):
        pass

    @tf.function
    def get_forward(self):
        return self.u

    @tf.function
    def get_backward(self):
        return
