"""
Custom RNN Cell definition.
Default RNNCell in TensorFlow throws errors when
variables are re-used between devices.
"""

from tensorflow.contrib.rnn import BasicRNNCell
from tensorflow.python.ops.math_ops import tanh
from tensorflow.python.ops import variable_scope as vs
from tensorflow.python.util import nest
from tensorflow.python.ops import math_ops
from tensorflow.python.ops import array_ops
import tensorflow as tf

from helper_routines import _variable_on_cpu

class CustomRNNCell(BasicRNNCell):
    """ This is a customRNNCell that allows the weights
    to be re-used on multiple devices. In particular, the Matrix of weights is
    set using _variable_on_cpu.
    The default version of the BasicRNNCell, did not support the ability to
    pin weights on one device (say cpu).
    """

    def __init__(self, num_units, activation = tf.nn.relu6, use_fp16 = False):
        self._num_units = num_units
        self._activation = activation
        self.use_fp16 = use_fp16

    def __call__(self, inputs, state, scope = None):
        """Most basic RNN:
        output = new_state = activation(W * input + U * state + B)."""
        with vs.variable_scope(scope or type(self).__name__):
            output = self._activation(_linear([inputs, state], self._num_units,
                                              True, use_fp16 = self.use_fp16))
        return output, output


class CustomRNNCell2(BasicRNNCell):
    """ This is a customRNNCell2 that allows the weights
    to be re-used on multiple devices. In particular, the Matrix of weights is
    set using _variable_on_cpu.
    The default version of the BasicRNNCell, did not support the ability to
    pin weights on one device (say cpu).
    """

    def __init__(self, num_units, activation = tf.nn.relu6, use_fp16 = False):
        self._num_units = num_units
        self.use_fp16 = use_fp16

    def __call__(self, inputs, state, scope = None):
        """Most basic RNN:
        output = new_state = activation(BN(W * input) + U * state + B).
         state dim: seq_len * num_units
         input dim: batch_size * feature_size
         W: feature_size * num_units
         U: num_units * num_units
        """
        with vs.variable_scope(scope or type(self).__name__):
            # print "rnn cell input size: ", inputs.get_shape().as_list()
            # print "rnn cell state size: ", state.get_shape().as_list()
            wsize = inputs.get_shape().as_list()[1]
            w = _variable_on_cpu('W', [wsize, self._num_units], use_fp16 = self.use_fp16)
            resi = math_ops.matmul(inputs, w)
            # batch_size * num_units

            bn_resi = seq_batch_norm(resi, n_out = self._num_units)
            usize = state.get_shape().as_list()[1]
            u = _variable_on_cpu('U', [usize, self._num_units], use_fp16 = self.use_fp16)
            resu = math_ops.matmul(state, u)
            bias = _variable_on_cpu('B', [self._num_units],
                                     tf.constant_initializer(0),
                                     use_fp16 = self.use_fp16)
            output = relux(tf.add(bn_resi, resu) + bias, capping = 20)
        return output, output


def relux(x, capping = None):
    """Clipped ReLU"""
    x = tf.nn.relu(x)
    if capping is not None:
        y = tf.minimum(x, capping)
    return y


def batch_norm(x, n_out, scope = None, is_train = True):
    """batch normalization"""
    with tf.variable_scope(scope or 'bn'):
        beta = _variable_on_cpu('beta', [n_out], initializer = tf.zeros_initializer())
        gamma = _variable_on_cpu('gamma', [n_out], initializer = tf.ones_initializer())
        batch_mean, batch_var = tf.nn.moments(x, [0, 1, 2], name = 'moments')
        ema = tf.train.ExponentialMovingAverage(decay = 0.5)
        def mean_var_with_update():
            ema_apply_op = ema.apply([batch_mean, batch_var])
            with tf.control_dependencies([ema_apply_op]):
                return tf.identity(batch_mean), tf.identity(batch_var)
        if is_train:
            mean, var = mean_var_with_update()
        else:
            mean, var = lambda : (ema.average(batch_mean), ema.average(batch_var))
        normed = tf.nn.batch_normalization(x, mean, var, beta, gamma, 1e-5)
    return normed


def seq_batch_norm(x, n_out, scope = None, is_train = True):
    """sequence batch normalization"""
    with tf.variable_scope(scope or 'sbn'):
        beta = _variable_on_cpu('beta', [n_out], initializer = tf.zeros_initializer())
        gamma = _variable_on_cpu('gamma', [n_out], initializer = tf.ones_initializer())
        batch_mean, batch_var = tf.nn.moments(x, [0], name = 'moments')
        ema = tf.train.ExponentialMovingAverage(decay = 0.5)
        def mean_var_with_update():
            ema_apply_op = ema.apply([batch_mean, batch_var])
            with tf.control_dependencies([ema_apply_op]):
                return tf.identity(batch_mean), tf.identity(batch_var)
        if is_train:
            mean, var = mean_var_with_update()
        else:
            mean, var = lambda : (ema.average(batch_mean), ema.average(batch_var))
        normed = tf.nn.batch_normalization(x, mean, var, beta, gamma, 1e-5)
    return normed


def _linear(args, output_size, bias, scope = None, use_fp16 = False):
    """Linear map: sum_i(args[i] * W[i]), where W[i] is a variable.

    Args:
      args: a 2D Tensor or a list of 2D, batch x n, Tensors.
      output_size: int, second dimension of W[i].
      bias: boolean, whether to add a bias term or not.
      bias_start: starting value to initialize the bias; 0 by default.
      scope: VariableScope for the created subgraph; defaults to "Linear".

    Returns:
      A 2D Tensor with shape [batch x output_size] equal to
      sum_i(args[i] * W[i]), where W[i]s are newly created matrices.

    Raises:
      ValueError: if some of the arguments has unspecified or wrong shape.
    """
    if args is None or (nest.is_sequence(args) and not args):
        raise ValueError("`args` must be specified")
    if not nest.is_sequence(args):
        args = [args]

    # Calculate the total size of arguments on dimension 1.
    total_arg_size = 0
    shapes = [a.get_shape().as_list() for a in args]
    for shape in shapes:
        if len(shape) != 2:
            raise ValueError(
                "Linear is expecting 2D arguments: %s" % str(shapes))
        if not shape[1]:
            raise ValueError(
                "Linear expects shape[1] of arguments: %s" % str(shapes))
        else:
            total_arg_size += shape[1]

    dtype = [a.dtype for a in args][0]

    # Now the computation.
    with vs.variable_scope(scope or "Linear"):
        matrix = _variable_on_cpu('Matrix', [total_arg_size, output_size],
                                  use_fp16 = use_fp16)
        if use_fp16:
            dtype = tf.float16
        else:
            dtype = tf.float32
        args = [tf.cast(x, dtype) for x in args]
        if len(args) == 1:
            res = math_ops.matmul(args[0], matrix)
        else:
            res = math_ops.matmul(array_ops.concat(args, 1), matrix)
        if not bias:
            return res
        bias_term = _variable_on_cpu('Bias', [output_size],
                                     tf.constant_initializer(0),
                                     use_fp16=use_fp16)
    return res + bias_term