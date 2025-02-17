"""Collection of tests for unified device functions."""

# global
import io
import multiprocessing
import os
import re
import sys
import time
from numbers import Number

import numpy as np
import nvidia_smi
import psutil
import pytest
from hypothesis import strategies as st, given

# local
import ivy
import ivy.functional.backends.numpy as ivy_np
import ivy_tests.test_ivy.helpers as helpers


# Tests #
# ------#

# Device Queries #

# dev


@given(
    array_shape=helpers.lists(
        arg=st.integers(2, 3),
        min_size="num_dims",
        max_size="num_dims",
        size_bounds=[1, 3],
    ),
    dtype=st.sampled_from(ivy_np.valid_numeric_dtypes),
    as_variable=st.booleans(),
)
def test_dev(array_shape, dtype, as_variable, fw, device):
    if fw == "torch" and "int" in dtype:
        return

    x = np.random.uniform(size=tuple(array_shape)).astype(dtype)
    x = ivy.asarray(x)
    if as_variable:
        x = ivy.variable(x)

    ret = ivy.dev(x)
    # type test
    assert isinstance(ret, str)
    # value test
    assert ret == device
    # array instance test
    assert x.dev() == device
    # container instance test
    container_x = ivy.Container({"a": x})
    assert container_x.dev() == device
    # container static test
    assert ivy.Container.static_dev(container_x) == device


# as_ivy_dev
@given(
    array_shape=helpers.lists(
        arg=st.integers(2, 3),
        min_size="num_dims",
        max_size="num_dims",
        size_bounds=[1, 3],
    ),
    dtype=st.sampled_from(ivy_np.valid_numeric_dtypes),
    as_variable=st.booleans(),
)
def test_as_ivy_dev(array_shape, dtype, as_variable, fw, device):
    if fw == "torch" and "int" in dtype:
        return

    x = np.random.uniform(size=tuple(array_shape)).astype(dtype)
    x = ivy.asarray(x)
    if as_variable:
        x = ivy.variable(x)

    if (isinstance(x, Number) or x.size == 0) and as_variable and fw == "mxnet":
        # mxnet does not support 0-dimensional variables
        return

    device = ivy.dev(x)
    ret = ivy.as_ivy_dev(device)
    # type test
    assert isinstance(ret, str)


# as_native_dev
@given(
    array_shape=helpers.lists(
        arg=st.integers(1, 3),
        min_size="num_dims",
        max_size="num_dims",
        size_bounds=[1, 3],
    ),
    dtype=st.sampled_from(ivy_np.valid_numeric_dtypes),
    as_variable=st.booleans(),
)
def test_as_native_dev(array_shape, dtype, as_variable, device, fw, call):
    if fw == "torch" and "int" in dtype:
        return

    x = np.random.uniform(size=tuple(array_shape)).astype(dtype)
    x = ivy.asarray(x)
    if as_variable:
        x = ivy.variable(x)

    if (isinstance(x, Number) or x.size == 0) and as_variable and fw == "mxnet":
        # mxnet does not support 0-dimensional variables
        return

    device = ivy.as_native_dev(device)
    ret = ivy.as_native_dev(ivy.dev(x))
    # value test
    if call in [helpers.tf_call, helpers.tf_graph_call]:
        assert "/" + ":".join(ret[1:].split(":")[-2:]) == "/" + ":".join(
            device[1:].split(":")[-2:]
        )
    elif call is helpers.torch_call:
        assert ret.type == device.type
    else:
        assert ret == device
    # compilation test
    if call is helpers.torch_call:
        # pytorch scripting does not handle converting string to device
        return


# memory_on_dev
@pytest.mark.parametrize("dev_to_check", ["cpu", "gpu:0"])
def test_memory_on_dev(dev_to_check, device, call):
    if "gpu" in dev_to_check and ivy.num_gpus() == 0:
        # cannot get amount of memory for gpu which is not present
        pytest.skip()
    ret = ivy.total_mem_on_dev(dev_to_check)
    # type test
    assert isinstance(ret, float)
    # value test
    assert 0 < ret < 64
    # compilation test
    if call is helpers.torch_call:
        # global variables aren't supported for pytorch scripting
        pytest.skip()


# Device Allocation #

# default_device
def test_default_device(device, call):
    # setting and unsetting
    orig_len = len(ivy.default_device_stack)
    ivy.set_default_device("cpu")
    assert len(ivy.default_device_stack) == orig_len + 1
    ivy.set_default_device("cpu")
    assert len(ivy.default_device_stack) == orig_len + 2
    ivy.unset_default_device()
    assert len(ivy.default_device_stack) == orig_len + 1
    ivy.unset_default_device()
    assert len(ivy.default_device_stack) == orig_len

    # with
    assert len(ivy.default_device_stack) == orig_len
    with ivy.DefaultDevice("cpu"):
        assert len(ivy.default_device_stack) == orig_len + 1
        with ivy.DefaultDevice("cpu"):
            assert len(ivy.default_device_stack) == orig_len + 2
        assert len(ivy.default_device_stack) == orig_len + 1
    assert len(ivy.default_device_stack) == orig_len


# to_dev
@given(
    array_shape=helpers.lists(
        arg=st.integers(1, 3),
        min_size="num_dims",
        max_size="num_dims",
        size_bounds=[1, 3],
    ),
    dtype=st.sampled_from(ivy_np.valid_numeric_dtypes),
    as_variable=st.booleans(),
    with_out=st.booleans(),
    stream=st.integers(0, 50),
)
def test_to_device(array_shape, dtype, as_variable, with_out, fw, device, call, stream):
    if fw == "torch" and "int" in dtype:
        return

    x = np.random.uniform(size=tuple(array_shape)).astype(dtype)
    x = ivy.asarray(x)
    if as_variable:
        x = ivy.variable(x)

    # create a dummy array for out that is broadcastable to x
    out = ivy.zeros(ivy.shape(x), device=device, dtype=dtype) if with_out else None

    device = ivy.dev(x)
    x_on_dev = ivy.to_device(x, device=device, stream=stream, out=out)
    dev_from_new_x = ivy.dev(x_on_dev)

    if with_out:
        # should be the same array test
        assert np.allclose(ivy.to_numpy(x_on_dev), ivy.to_numpy(out))

        # should be the same device
        assert ivy.dev(x_on_dev, as_native=True) == ivy.dev(out, as_native=True)

        # check if native arrays are the same
        if ivy.current_backend_str() in ["tensorflow", "jax"]:
            # these backends do not support native inplace updates
            return

        assert x_on_dev.data is out.data

    # value test
    if call in [helpers.tf_call, helpers.tf_graph_call]:
        assert "/" + ":".join(dev_from_new_x[1:].split(":")[-2:]) == "/" + ":".join(
            device[1:].split(":")[-2:]
        )
    elif call is helpers.torch_call:
        assert type(dev_from_new_x) == type(device)
    else:
        assert dev_from_new_x == device

    # array instance test
    assert x.to_device(device).dev() == device
    # container instance test
    container_x = ivy.Container({"x": x})
    assert container_x.to_device(device).dev() == device
    # container static test
    assert ivy.Container.to_device(container_x, device).dev() == device


# Function Splitting #


@st.composite
def _axis(draw):
    max_val = draw(st.shared(st.integers(), key="num_dims"))
    return draw(st.integers(0, max_val - 1))


@given(
    array_shape=helpers.lists(
        arg=st.integers(1, 3),
        min_size="num_dims",
        max_size="num_dims",
        size_bounds=[1, 3],
    ),
    dtype=st.sampled_from(ivy_np.valid_numeric_dtypes),
    as_variable=st.booleans(),
    chunk_size=st.integers(1, 3),
    axis=_axis(),
)
def test_split_func_call(
    array_shape, dtype, as_variable, chunk_size, axis, fw, device, call
):
    if fw == "torch" and "int" in dtype:
        return

    # inputs
    shape = tuple(array_shape)
    x1 = np.random.uniform(size=shape).astype(dtype)
    x2 = np.random.uniform(size=shape).astype(dtype)
    x1 = ivy.asarray(x1)
    x2 = ivy.asarray(x2)
    if as_variable:
        x1 = ivy.variable(x1)
        x2 = ivy.variable(x2)

    # function
    def func(t0, t1):
        return t0 * t1, t0 - t1, t1 - t0

    # predictions
    a, b, c = ivy.split_func_call(
        func, [x1, x2], "concat", chunk_size=chunk_size, input_axes=axis
    )

    # true
    a_true, b_true, c_true = func(x1, x2)

    # value test
    assert np.allclose(ivy.to_numpy(a), ivy.to_numpy(a_true))
    assert np.allclose(ivy.to_numpy(b), ivy.to_numpy(b_true))
    assert np.allclose(ivy.to_numpy(c), ivy.to_numpy(c_true))


@given(
    array_shape=helpers.lists(
        arg=st.integers(2, 3),
        min_size="num_dims",
        max_size="num_dims",
        size_bounds=[2, 3],
    ),
    dtype=st.sampled_from(ivy_np.valid_numeric_dtypes),
    as_variable=st.booleans(),
    chunk_size=st.integers(1, 3),
    axis=st.integers(0, 1),
)
def test_split_func_call_with_cont_input(
    array_shape, dtype, as_variable, chunk_size, axis, fw, device, call
):
    # Skipping some dtype for certain frameworks
    if (
        (fw == "torch" and "int" in dtype)
        or (fw == "numpy" and "float16" in dtype)
        or (fw == "tensorflow" and "u" in dtype)
    ):
        return
    shape = tuple(array_shape)
    x1 = np.random.uniform(size=shape).astype(dtype)
    x2 = np.random.uniform(size=shape).astype(dtype)
    x1 = ivy.asarray(x1, device=device)
    x2 = ivy.asarray(x2, device=device)
    # inputs

    if as_variable:
        in0 = ivy.Container(cont_key=ivy.variable(x1))
        in1 = ivy.Container(cont_key=ivy.variable(x2))
    else:
        in0 = ivy.Container(cont_key=x1)
        in1 = ivy.Container(cont_key=x2)

    # function
    def func(t0, t1):
        return t0 * t1, t0 - t1, t1 - t0

    # predictions
    a, b, c = ivy.split_func_call(
        func, [in0, in1], "concat", chunk_size=chunk_size, input_axes=axis
    )

    # true
    a_true, b_true, c_true = func(in0, in1)

    # value test
    assert np.allclose(ivy.to_numpy(a.cont_key), ivy.to_numpy(a_true.cont_key))
    assert np.allclose(ivy.to_numpy(b.cont_key), ivy.to_numpy(b_true.cont_key))
    assert np.allclose(ivy.to_numpy(c.cont_key), ivy.to_numpy(c_true.cont_key))


# profiler
def test_profiler(device, call):
    # ToDo: find way to prevent this test from hanging when run
    #  alongside other tests in parallel

    # log dir
    this_dir = os.path.dirname(os.path.realpath(__file__))
    log_dir = os.path.join(this_dir, "../log")

    # with statement
    with ivy.Profiler(log_dir):
        a = ivy.ones([10])
        b = ivy.zeros([10])
        a + b
    if call is helpers.mx_call:
        time.sleep(1)  # required by MXNet for some reason

    # start and stop methods
    profiler = ivy.Profiler(log_dir)
    profiler.start()
    a = ivy.ones([10])
    b = ivy.zeros([10])
    a + b
    profiler.stop()
    if call is helpers.mx_call:
        time.sleep(1)  # required by MXNet for some reason


@given(num=st.integers(0, 5))
def test_num_arrays_on_dev(num, device):
    arrays = [
        ivy.array(np.random.uniform(size=2).tolist(), device=device) for _ in range(num)
    ]
    assert ivy.num_ivy_arrays_on_dev(device) == num
    for item in arrays:
        del item


@given(num=st.integers(0, 5))
def test_get_all_arrays_on_dev(num, device):
    arrays = [ivy.array(np.random.uniform(size=2)) for _ in range(num)]
    arr_ids_on_dev = [id(a) for a in ivy.get_all_ivy_arrays_on_dev(device).values()]
    for a in arrays:
        assert id(a) in arr_ids_on_dev


@given(num=st.integers(0, 2), attr_only=st.booleans())
def test_print_all_ivy_arrays_on_dev(num, device, attr_only):
    arr = [ivy.array(np.random.uniform(size=2)) for _ in range(num)]

    # Flush to avoid artifact
    sys.stdout.flush()
    # temporarily redirect output to a buffer
    captured_output = io.StringIO()
    sys.stdout = captured_output

    ivy.print_all_ivy_arrays_on_dev(device, attr_only=attr_only)
    # Flush again to make sure all data is printed
    sys.stdout.flush()
    written = captured_output.getvalue().splitlines()
    # restore stdout
    sys.stdout = sys.__stdout__

    # Should have written same number of lines as the number of array in device
    assert len(written) == num

    if attr_only:
        # Check that the attribute are printed are in the format of ((dim,...), type)
        regex = r"^\(\((\d+,(\d,\d*)*)\), \'\w*\'\)$"
    else:
        # Check that the arrays are printed are in the format of ivy.array(...)
        regex = r"^ivy\.array\(\[.*\]\)$"

    # Clear the array from device
    for item in arr:
        del item

    # Apply the regex search
    assert all([re.match(regex, line) for line in written])


def test_total_mem_on_dev(device):
    if "cpu" in device:
        assert ivy.total_mem_on_dev(device) == psutil.virtual_memory().total / 1e9
    elif "gpu" in device:
        gpu_mem = nvidia_smi.nvmlDeviceGetMemoryInfo(device)
        assert ivy.total_mem_on_dev(device) == gpu_mem / 1e9


def test_gpu_is_available(fw):
    # If gpu is available but cannot be initialised it will fail the test
    if ivy.gpu_is_available():
        try:
            nvidia_smi.nvmlInit()
        except (
            nvidia_smi.NVMLError_LibraryNotFound,
            nvidia_smi.NVMLError_DriverNotLoaded,
        ):
            assert False


def test_num_cpu_cores():
    # using multiprocessing module too because ivy uses psutil as basis.
    p_cpu_cores = psutil.cpu_count()
    m_cpu_cores = multiprocessing.cpu_count()
    assert type(ivy.num_cpu_cores()) == int
    assert ivy.num_cpu_cores() == p_cpu_cores
    assert ivy.num_cpu_cores() == m_cpu_cores


# Still to Add #
# ---------------#


# clear_mem_on_dev
# used_mem_on_dev # working fine for cpu
# percent_used_mem_on_dev # working fine for cpu
# dev_util # working fine for cpu
# tpu_is_available
# _assert_dev_correct_formatting
# class Profiler
