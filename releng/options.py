"""
Handling of Jenkins build options

This module provides a method for processing build options to initialize
the build environment and parameters, and helper classes used by it.
It is is only used internally within the releng package.
"""

import shlex

from common import ConfigurationError
from common import BuildType, FftLibrary, Simd
from environment import BuildEnvironment
from parameters import BuildParameters

class _OptionHandlerClosure(object):
    """Helper class for providing context for build option handler methods.

    This class provides methods that are used as build option handlers for
    cases that cannot be directly call methods in BuildEnvironment.
    It essentially just captures the environment and parameter objects from
    the scope that creates it, and provides methods that can then be called
    without explicitly passing these objects around to each.
    """

    def __init__(self, env, params):
        self._env = env
        self._params = params

    # Please keep the handlers in the same order as in process_build_options().

    def _init_build_jobs(self, value):
        self._env._build_jobs = int(value)

    def _init_phi(self):
        self._env._init_phi()
        self._params.phi = True

    def _init_mdrun_only(self):
        self._params.mdrun_only = True

    def _init_reference(self):
        self._params.build_type = BuildType.REFERENCE

    def _init_release(self):
        self._params.build_type = BuildType.OPTIMIZED

    def _init_asan(self):
        self._params.build_type = BuildType.ASAN

    def _init_tsan(self):
        self._env._init_tsan()
        self._params.build_type = BuildType.TSAN

    def _init_atlas(self):
        self._env._init_atlas()
        self._params.external_linalg = True

    def _init_mkl(self):
        self._params.fft_library = FftLibrary.MKL
        self._params.external_linalg = True

    def _init_fftpack(self):
        self._params.fft_library = FftLibrary.FFTPACK

    def _init_double(self):
        self._params.double = True

    def _init_x11(self):
        self._params.x11 = True

    def _init_simd(self, simd):
        self._params.simd = Simd.parse(simd)

    def _init_thread_mpi(self, value):
        self._params.thread_mpi = value

    def _init_gpu(self, value):
        self._params.gpu = value

    def _init_mpi(self, value):
        if value:
            self._env._init_mpi(self._params.gpu)
        self._params.mpi = value

    def _init_openmp(self, value):
        self._params.openmp = value

    def _init_valgrind(self):
        self._params.memcheck = True

    def _add_env_var(self, assignment):
        var, value = assignment.split('=', 1)
        self._env.add_env_var(var, value)

    def _add_cmake_option(self, assignment):
        var, value = assignment.split('=', 1)
        self._params.extra_cmake_options[var] = value

    def _add_gmxtest_args(self, args):
        self._params.extra_gmxtest_args.extend(shlex.split(args))

class _BuildOptionHandler(object):
    """Base class for build options.

    Concrete option classes implement matches() and handle() methods to
    identify and handle the option.
    """

    def __init__(self, name, handler, allow_multiple=False):
        """Creates a handler for a specified option.

        Args:
            name (str): Name of the option. Exact interpretation depends on the
                subclass.
            handler (function): Handler function to call when the option is set.
                Parameters may be passed to the handler to provide information
                parsed from the option string (e.g., a version number),
                depending on the subclass.
            allow_multiple (bool): If not True, at most one option is allowed
                to match this handler.
        """
        self._name = name
        self._handler = handler
        self.allow_multiple = allow_multiple

    def matches(self, opt):
        """Checks whether this handler handles the provided option.

        If this method returns True, then handle() will be called to process
        the option.

        Args:
            opt (str): Option to match.

        Returns:
            bool: Whether this handler should handle opt.
        """
        return False

    def handle(self, opt):
        """Handles the provided option.

        This method is called for each option for which matches() returns True.
        It calls the handler provided to the constructor, possibly after
        parsing information from the option name.

        Args:
            opt (str): Option to process.
        """
        pass

class _SimpleOptionHandler(_BuildOptionHandler):
    """Handler for a simple flag option.

    This is provided for cases where the option just turns on or selects a
    specific feature and the negation would not make much sense, and so
    _BoolOptionHandler would not be appropriate.

    The handler provided to the constructor is called without parameters.
    """

    def matches(self, opt):
        return opt == self._name

    def handle(self, opt):
        self._handler()

class _SuffixOptionHandler(_BuildOptionHandler):
    """Handler for an option with syntax 'opt-VALUE'.

    The handler provided to the constructor is called with a single string
    parameter that provides VALUE.
    """

    def matches(self, opt):
        return opt.startswith(self._name)

    def handle(self, opt):
        suffix = opt[len(self._name):]
        self._handler(suffix)

class _BoolOptionHandler(_BuildOptionHandler):
    """Handler for an option with syntax '[no-]opt[=on/off]'.

    The handler provided to the constructor is called with a single boolean
    parameter that identifies whether the option is on or off.
    """

    def matches(self, opt):
        return opt in (self._name, 'no-' + self._name) \
                or opt.startswith(self._name + '=')

    def handle(self, opt):
        self._handler(self._parse(opt))

    def _parse(self, opt):
        if opt == self._name:
            return True
        if opt == 'no-' + self._name:
            return False
        if opt.startswith(self._name + '='):
            value = opt[len(self._name)+1:].lower()
            if value in ('1', 'on', 'true'):
                return True
            if value in ('0', 'off', 'false'):
                return False
        raise ConfigurationError('invalid build option: ' + opt)

def process_build_options(system, opts):
    """Initializes build environment and parameters from OS and build options.

    Creates the environment and parameters objects, and adjusts them
    based on the provided options.

    Args:
        system (str or None): Operating system of the build node.
        opts (List[str]): List of build options.

    Returns:
        Tuple[BuildEnvironment, BuildParameters]: Build environment and
            parameters initialized according to the options.
    """
    e = BuildEnvironment(system)
    p = BuildParameters()
    if not opts:
        return (e, p)
    h = _OptionHandlerClosure(e, p)
    # The options are processed in the order they are in the tuple, to support
    # cross-dependencies between the options (there are a few).
    # If you add options here, please also update the documentation for the
    # options in docs/releng.rst.
    handlers = (
            _SuffixOptionHandler('build-jobs=', h._init_build_jobs),
            _SuffixOptionHandler('cmake-', e._init_cmake),
            _SuffixOptionHandler('gcc-', e.init_gcc),
            _SuffixOptionHandler('clang-', e.init_clang),
            _SuffixOptionHandler('icc-', e._init_icc),
            _SuffixOptionHandler('msvc-', e._init_msvc),
            _SuffixOptionHandler('cuda-', e._init_cuda),
            _SimpleOptionHandler('phi', h._init_phi),
            _SimpleOptionHandler('mdrun-only', h._init_mdrun_only),
            _SimpleOptionHandler('reference', h._init_reference),
            _SimpleOptionHandler('release', h._init_release),
            _SimpleOptionHandler('asan', h._init_asan),
            _SimpleOptionHandler('tsan', h._init_tsan),
            _SimpleOptionHandler('atlas', h._init_atlas),
            _SimpleOptionHandler('mkl', h._init_mkl),
            _SimpleOptionHandler('fftpack', h._init_fftpack),
            _SimpleOptionHandler('double', h._init_double),
            _SimpleOptionHandler('x11', h._init_x11),
            _SuffixOptionHandler('simd=', h._init_simd),
            _BoolOptionHandler('thread-mpi', h._init_thread_mpi),
            _BoolOptionHandler('gpu', h._init_gpu),
            _BoolOptionHandler('mpi', h._init_mpi),
            _BoolOptionHandler('openmp', h._init_openmp),
            _SimpleOptionHandler('valgrind', h._init_valgrind),
            _SuffixOptionHandler('env+', h._add_env_var, allow_multiple=True),
            _SuffixOptionHandler('cmake+', h._add_cmake_option, allow_multiple=True),
            _SuffixOptionHandler('gmxtest+', h._add_gmxtest_args, allow_multiple=True),
        )
    opts = list(opts)
    for handler in handlers:
        found_opts = [x for x in opts if handler.matches(x)]
        if not handler.allow_multiple and len(found_opts) > 1:
            raise ConfigurationError('conflicting options found: ' + ' '.join(found_opts))
        for found_opt in found_opts:
            opts.remove(found_opt)
            handler.handle(found_opt)
    if opts:
        raise ConfigurationError('unknown options: ' + ' '.join(opts))
    return (e, p)