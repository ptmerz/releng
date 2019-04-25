import os.path
import unittest
from unittest import mock

from releng.common import JobType
from releng.context import BuildContext

from releng.test.utils import TestHelper

class TestRunBuild(unittest.TestCase):
    def setUp(self):
        self.helper = TestHelper(self)

    def test_NoOptions(self):
        self.helper.add_input_file('script/build.py',
                """\
                def do_build(context):
                    pass
                """)
        BuildContext._run_build(self.helper.factory,
                'script/build.py', JobType.GERRIT, None)

    def test_ScriptOptions(self):
        self.helper.add_input_file('script/build.py',
                """\
                build_options = ['gcc-4.8']
                def do_build(context):
                    pass
                """)
        BuildContext._run_build(self.helper.factory,
                'script/build.py', JobType.GERRIT, None)

    def test_MixedOptions(self):
        self.helper.add_input_file('script/build.py',
                """\
                build_options = ['gcc-4.8']
                def do_build(context):
                    pass
                """)
        BuildContext._run_build(self.helper.factory,
                'script/build.py', JobType.GERRIT, ['build-jobs=3'])

    def test_ExtraOptions(self):
        self.helper.add_input_file('script/build.py',
                """\
                TestEnum = Enum.create('TestEnum', 'foo', 'bar')
                extra_options = {
                    'extra': Option.simple,
                    'enum': Option.enum(TestEnum)
                }
                def do_build(context):
                    pass
                """)
        BuildContext._run_build(self.helper.factory,
                'script/build.py', JobType.GERRIT, ['extra', 'enum=foo'])

    def test_Parameters(self):
        self.helper.add_input_file('script/build.py',
                """\
                def do_build(context):
                    context.params.get('PARAM', Parameter.bool)
                """)
        BuildContext._run_build(self.helper.factory,
                'script/build.py', JobType.GERRIT, None)


class TestReadBuildScriptConfig(unittest.TestCase):
    def setUp(self):
        self.helper = TestHelper(self)

    def test_ClangAnalyzer(self):
        self.helper.add_input_file('script/build.py',
                """\
                build_options = ['clang-3.8', 'clang-static-analyzer-3.8']
                def do_build(context):
                    pass
                """)
        result = BuildContext._read_build_script_config(self.helper.factory,
                'script/build.py')
        self.assertEqual(result, {
                'opts': ['clang-3.8', 'clang-static-analyzer-3.8'],
                'host': 'bs_nix-static_analyzer',
                'labels': 'clang-3.8 && clang-static-analyzer-3.8'
            })

    def test_CudaGpuBuild(self):
        self.helper.add_input_file('script/build.py',
                """\
                build_options = [ 'gcc-4.9', 'cuda-9.0', 'gpuhw=nvidia' ]
                def do_build(context):
                    pass
                """)
        result = BuildContext._read_build_script_config(self.helper.factory,
                'script/build.py')
        self.assertEqual(result, {
                'opts': [ 'gcc-4.9', 'cuda-9.0', 'gpuhw=nvidia' ],
                'host': 'bs_nix1310',
                'labels': 'cuda-9.0 && gcc-4.9 && nvidia'
            })



class TestReadCmakeVariableFile(unittest.TestCase):
    def setUp(self):
        self.helper = TestHelper(self)

    def test_ReadFile(self):
        self.helper.add_input_file('Test.cmake',
                """\
                set(FOO "1")
                set(BAR "2")
                """)
        context = self.helper.factory.create_context(JobType.GERRIT, None, None)
        result = context.read_cmake_variable_file('Test.cmake')
        self.assertEqual(result, { 'FOO': '1', 'BAR': '2' })

if __name__ == '__main__':
    unittest.main()
