"""
Build and Jenkins environment handling

This file contains all the code that hardcodes details about the Jenkins build
slave environment, such as paths to various executables.
"""

import os

from common import ConfigurationError
from common import Compiler,System
import cmake
import slaves

# TODO: Check that the paths returned/used actually exists and raise nice
# errors instead of mysteriously breaking builds if the node configuration is
# not right.
# TODO: Clean up the different mechanisms used here; even for the ~same thing,
# different approaches may be used (some might set an environment variable,
# others use an absolute path, or set a CMake option).

def _to_version_tuple(version_string):
    return [int(x) for x in version_string.split('.')]

def _is_older_version(older, newer):
    return _to_version_tuple(older) < _to_version_tuple(newer)

class BuildEnvironment(object):
    """Provides access to the build environment.

    Most details of the build environment are handled transparently based on
    the provided build options, and the build script does not need to consider
    this.  For build scripts, the main interface this class provides is to find
    locations of some special executables (such as cppcheck) that may be needed
    for the build.  Compiler selection is handled without special action from
    build scripts.

    In rare cases, the build scripts may benefit from inspecting the attributes
    in this class to determine, e.g., the operating system running the build or
    the compiler being used.

    Attributes:
       system (System): Operating system of the build node.
       compiler (Compiler or None): Selected compiler.
       compiler_version (string): Version number for the selected compiler.
       c_compiler (str or None): Name of the C compiler executable.
       cxx_compiler (str or None): Name of the C++ compiler executable.
       gcov_command (str): Name of the gcov executable.
       cmake_command (str): Name of the CMake executable.
       ctest_command (str): Name of the CTest executable.
       cmake_version (str): Version of the CMake executable.
       cmake_generator (str or None): CMake generator being used.
       cuda_root (str or None): Root of the CUDA toolkit being used
           (for passing to CUDA_TOOLKIT_ROOT_DIR CMake option).
       cuda_host_compiler (str or None): Full path to the host compiler used
           with CUDA (for passing to CUDA_HOST_COMPILER CMake option).
       amdappsdk_root (str or None): Root of the AMD SDK being used
           (for using as AMDAPPSDKROOT environment variable).
       extra_cmake_options (Dict[str, str]): Additional options to pass to
           CMake.
    """

    def __init__(self, factory):
        self.system = factory.system
        self.compiler = None
        self.compiler_version = None
        self.c_compiler = None
        self.cxx_compiler = None
        self.gcov_command = None
        self.cmake_command = 'cmake'
        self.ctest_command = 'ctest'
        self.cmake_version = None
        self.cmake_generator = None
        self.cuda_root = None
        self.cuda_host_compiler = None
        self.amdappsdk_root = None
        self.clang_analyzer_output_dir = None
        self.extra_cmake_options = dict()

        self._build_jobs = 1
        self._build_prefix_cmd = None
        self._cmd_runner = factory.cmd_runner
        self._workspace = factory.workspace
        self._cmake_base_dir = None

        if self.system is not None:
            self._init_system()

    def get_cppcheck_command(self, version):
        """Returns path to the cppcheck executable of given version.

        Args:
            version (str): cppcheck version to use.
        """
        return os.path.expanduser('~/bin/cppcheck-{0}'.format(version))

    def get_doxygen_command(self, version):
        """Returns path to the Doxygen executable of given version.

        Args:
            version (str): Doxygen version to use.
        """
        return os.path.expanduser('~/tools/doxygen-{0}/bin/doxygen'.format(version))

    def get_uncrustify_command(self):
        """Returns path to the uncrustify executable."""
        return os.path.expanduser('~/bin/uncrustify')

    def _get_build_cmd(self, target=None, parallel=True, keep_going=False):
        cmd = []
        if self._build_prefix_cmd is not None:
            cmd.extend(self._build_prefix_cmd)
        cmd.extend([self.cmake_command, '--build', '.'])
        if target is not None:
            cmd.extend(['--target', target])
        jobs = self._build_jobs if parallel else 1
        cmd.extend(['--', '-j{0}'.format(jobs)])
        if keep_going:
            cmd.append('-k')
        return cmd

    def _set_cmake_minimum_version(self, version):
        if self.cmake_version or not version:
            return
        current_version = cmake.get_cmake_version(self._cmd_runner, self.cmake_command)
        self.cmake_version = current_version
        if not _is_older_version(current_version, version):
            return
        available_versions = self._get_available_cmake_versions()
        for test_version in available_versions:
            if not _is_older_version(test_version, version):
                self._init_cmake(test_version)

    def _get_available_cmake_versions(self):
        versions = []
        for cmake_dir in os.listdir(self._cmake_base_dir):
            cmd = os.path.join(self._cmake_base_dir, cmake_dir, 'bin', 'cmake')
            if self.system == System.WINDOWS and not cmd.endswith('.exe'):
                cmd += '.exe'
            if os.path.isfile(cmd):
                versions.append(cmake_dir.split("-")[-1])
        versions.sort(key=_to_version_tuple)
        return versions

    def set_env_var(self, variable, value):
        """Sets environment variable to be used for further commands.

        All subsequent commands run with BuildContext.run_cmd() etc. will use
        the environment variable.

        Args:
            variable (str): Name of environment variable to set.
            value (str): Value to set the variable to.  As a convenience, if
                the value is None, nothing is done.
        """
        self._cmd_runner.set_env_var(variable, value)

    def append_to_env_var(self, variable, value):
        self._cmd_runner.append_to_env_var(variable, value)

    def prepend_path_env(self, path):
        """Prepends a path to the executable search path (PATH)."""
        self._cmd_runner.prepend_to_env_var('PATH', os.path.expanduser(path), sep=os.pathsep)

    def append_path_env(self, path):
        """Appends a path to the executable search path (PATH)."""
        self._cmd_runner.append_to_env_var('PATH', os.path.expanduser(path), sep=os.pathsep)

    def run_env_script(self, env_cmd):
        self._cmd_runner.import_env(env_cmd, self.cmake_command)

    def _init_system(self):
        if self.system == System.WINDOWS:
            self.cmake_generator = 'NMake Makefiles JOM'
            self._build_jobs = 4
            self._cmake_base_dir = 'c:\\utils'
        else:
            self._build_jobs = 2
            self.prepend_path_env('~/bin')
            if self.system == System.OSX:
                self.set_env_var('CMAKE_PREFIX_PATH', '/opt/local')
            self._init_core_dump()
            self._cmake_base_dir = ('/opt/cmake')

    def _init_core_dump(self):
        import resource
        try:
            limits = (resource.RLIM_INFINITY, resource.RLIM_INFINITY)
            resource.setrlimit(resource.RLIMIT_CORE, limits)
        except:
            pass

    # Methods from here down are used as build option handlers in options.py.
    # Please keep them in the same order as in process_build_options().

    def _set_build_jobs(self, jobs):
        self._build_jobs = jobs

    def _init_cmake(self, version):
        cmake_bin_dir = os.path.join(self._cmake_base_dir, 'cmake-' + version, 'bin')
        if not os.path.exists(cmake_bin_dir):
            cmake_bin_dir = os.path.join(self._cmake_base_dir, version, 'bin')
        self.cmake_command = os.path.join(cmake_bin_dir, 'cmake')
        self.ctest_command = os.path.join(cmake_bin_dir, 'ctest')
        self.cmake_version = version

    def _init_gcc(self, version):
        """Initializes the build to use given gcc version as the compiler.

        This method is called internally if the build options set the compiler
        (with gcc-X.Y), but it can also be called directly from a build script
        if the build does not use options.

        Args:
            version (str): GCC version number (major.minor) to use.
        """
        self.compiler = Compiler.GCC
        self.compiler_version = version
        self.c_compiler = 'gcc-' + version
        self.cxx_compiler = 'g++-' + version
        self.gcov_command = 'gcov-' + version

    def _manage_stdlib_from_gcc(self, format_for_stdlib_flag):
        """Manages using a C++ standard library from a particular gcc toolchain

        Use this function to configure compilers (e.g. icc or clang) to
        use the standard library from a particular gcc installation on the
        particular host in use, since the system gcc may be too old.
        """

        # TODO should setting gcctoolchain go in node-specific
        # setup somewhere? Or the C++ standard library become
        # a build option?
        gcctoolchainpath=None
        if os.getenv('NODE_NAME') == slaves.BS_CENTOS63:
            gcctoolchainpath='/opt/gcc/5.2.0'
        if os.getenv('NODE_NAME') == slaves.BS_MIC:
            # icc is used here, and is buggy with respect to libstdc++ in gcc-5
            gcctoolchainpath='/opt/gcc/4.9.3'

        if gcctoolchainpath:
            stdlibflag=format_for_stdlib_flag.format(gcctoolchain=gcctoolchainpath)
            self.append_to_env_var('CFLAGS', stdlibflag)
            self.append_to_env_var('CXXFLAGS', stdlibflag)
            format_for_linker_flags="-Wl,-rpath,{gcctoolchain}/lib64 -L{gcctoolchain}/lib64"
            self.extra_cmake_options['CMAKE_CXX_LINK_FLAGS'] = format_for_linker_flags.format(gcctoolchain=gcctoolchainpath)

    def _init_clang(self, version):
        """Initializes the build to use given clang version as the compiler.

        This method is called internally if the build options set the compiler
        (with clang-X.Y), but it can also be called directly from a build
        script if the build does not use options.

        Args:
            version (str): clang version number (major.minor) to use.
        """
        self.compiler = Compiler.CLANG
        self.compiler_version = version
        self.c_compiler = 'clang-' + version
        self.cxx_compiler = 'clang++-' + version
        # Need a suitable standard library for C++11 support, so get
        # one from a gcc on the host.
        self._manage_stdlib_from_gcc('--gcc-toolchain={gcctoolchain}')
        # Symbolizer is only required for ASAN builds, but should not do any
        # harm to always set it (and that is much simpler).
        clang_path = self._cmd_runner.find_executable(self.c_compiler)
        clang_path = os.path.dirname(os.path.realpath(clang_path))
        symbolizer_path = os.path.join(clang_path, 'llvm-symbolizer')
        self.set_env_var('ASAN_SYMBOLIZER_PATH', symbolizer_path)

    def _init_icc(self, version):
        if self.system == System.WINDOWS:
            if self.compiler is None or self.compiler != Compiler.MSVC:
                raise ConfigurationError('need to specify msvc version for icc on Windows')
            self.c_compiler = 'icl'
            self.cxx_compiler = 'icl'
            self.extra_cmake_options['CMAKE_EXE_LINKER_FLAGS'] = '"/machine:x64"'
            if version == '15.0':
                self.run_env_script(r'"C:\Program Files (x86)\Intel\Composer XE 2015\bin\compilervars.bat" intel64 vs' + self.compiler_version)
            elif version == '16.0':
                self.run_env_script(r'"C:\Program Files (x86)\IntelSWTools\compilers_and_libraries_2016\windows\bin\compilervars.bat" intel64 vs' + self.compiler_version)
            else:
                raise ConfigurationError('only icc 16.0 is supported for Windows builds with the Intel compiler')
        else:
            self.c_compiler = 'icc'
            self.cxx_compiler = 'icpc'
            if version == '16.0':
                self.run_env_script('. /opt/intel/compilers_and_libraries_2016/linux/bin/compilervars.sh intel64')
            elif version == '15.0':
                self.run_env_script('. /opt/intel/composer_xe_2015/bin/compilervars.sh intel64')
            elif version == '14.0':
                self.run_env_script('. /opt/intel/composer_xe_2013_sp1/bin/compilervars.sh intel64')
            elif version == '13.0':
                self.run_env_script('. /opt/intel/composer_xe_2013/bin/compilervars.sh intel64')
            elif version == '12.1':
                self.run_env_script('. /opt/intel/composer_xe_2011_sp1/bin/compilervars.sh intel64')
            else:
                raise ConfigurationError('only icc 12.1, 13.0, 14.0, 15.0, 16.0 are supported, got icc-' + version)

            # Need a suitable standard library for C++11 support.
            # icc on Linux is required to use the C++ headers and
            # standard libraries from a gcc installation, and defaults
            # to that of the gcc it finds in the path.
            self._manage_stdlib_from_gcc('-gcc-name={gcctoolchain}/bin/gcc')
        self.compiler = Compiler.INTEL
        self.compiler_version = version

    def _init_msvc(self, version):
        self.compiler = Compiler.MSVC
        self.compiler_version = version
        if version == '2010':
            self.run_env_script(r'"C:\Program Files (x86)\Microsoft Visual Studio 10.0\VC\vcvarsall.bat" amd64')
        elif version == '2013':
            self.run_env_script(r'"C:\Program Files (x86)\Microsoft Visual Studio 12.0\VC\vcvarsall.bat" amd64')
        elif version == '2015':
            self.run_env_script(r'"C:\Program Files (x86)\Microsoft Visual Studio 14.0\VC\vcvarsall.bat" amd64')
        else:
            raise ConfigurationError('only Visual Studio 2010, 2013, and 2015 are supported, got msvc-' + version)

    def _init_clang_static_analyzer(self, version):
        scan_build = 'scan-build-' + version
        cxx_analyzer = 'c++-analyzer-' + version
        html_output_dir = self._workspace.get_log_dir(category='scan_html')
        self.clang_analyzer_output_dir = html_output_dir
        self.set_env_var('CCC_CC', self.c_compiler)
        self.set_env_var('CCC_CXX', self.cxx_compiler)
        self.cxx_compiler = cxx_analyzer
        self._build_prefix_cmd = [scan_build,
                '-o', html_output_dir]
        self.extra_cmake_options['GMX_STDLIB_CXX_FLAGS'] = '-stdlib=libc++'
        self.extra_cmake_options['GMX_STDLIB_LIBRARIES'] = '-lc++abi -lc++'

    def _init_cuda(self, version):
        self.cuda_root = '/opt/cuda_' + version

    def _init_amdappsdk(self, version):
        self.amdappsdk_root = '/opt/AMDAPPSDK-' + version

    def _init_phi(self):
        self.extra_cmake_options['CMAKE_PREFIX_PATH'] = os.path.expanduser('~/utils/libxml2')

    def _init_tsan(self):
        self.set_env_var('LD_LIBRARY_PATH', os.path.expanduser('~/tools/gcc-nofutex/lib64'))

    def _init_atlas(self):
        self.set_env_var('CMAKE_LIBRARY_PATH', '/usr/lib/atlas-base')

    def _init_mpi(self):
        # Set the host compiler to the underlying compiler.
        # Normally, C++ compiler should be used, but nvcc <=v5.0 does not
        # recognize icpc, only icc, so for simplicity the C compiler is used
        # for all cases, as it works as well.
        if self.compiler in (Compiler.GCC, Compiler.INTEL) and self.system != System.WINDOWS:
            c_compiler_path = self._cmd_runner.find_executable(self.c_compiler)
            if not c_compiler_path:
                raise ConfigurationError("Could not determine the full path to the compiler ({0})".format(self.c_compiler))
            self.cuda_host_compiler = c_compiler_path
        self.set_env_var('OMPI_CC', self.c_compiler)
        self.set_env_var('OMPI_CXX', self.cxx_compiler)
        self.c_compiler = 'mpicc'
        self.cxx_compiler = 'mpic++'
