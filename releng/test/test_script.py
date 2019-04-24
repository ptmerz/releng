import os.path
import unittest
from unittest import mock

from releng.common import Project
from releng.script import BuildScript

from releng.test.utils import TestHelper

class TestBuildScript(unittest.TestCase):
    def setUp(self):
        self.helper = TestHelper(self)

    def test_EmptyScript(self):
        executor = self.helper.executor
        self.helper.add_input_file('build.py',
                """\
                def do_build(context):
                    pass
                """);
        script = BuildScript(executor, 'build.py')
        self.assertEqual(script.settings.build_opts, [])
        self.assertFalse(script.settings.build_out_of_source)
        self.assertEqual(script.settings.extra_projects, [])

    def test_SetGlobals(self):
        executor = self.helper.executor
        self.helper.add_input_file('build.py',
                """\
                build_options = ['foo', 'bar']
                build_out_of_source = True
                extra_projects = [Project.REGRESSIONTESTS]
                use_stdlib_through_env_vars = False
                def do_build(context):
                    pass
                """);
        script = BuildScript(executor, 'build.py')
        self.assertEqual(script.settings.build_opts, ['foo', 'bar'])
        self.assertTrue(script.settings.build_out_of_source)
        self.assertEqual(script.settings.extra_projects, [Project.REGRESSIONTESTS])
        self.assertFalse(script.settings.use_stdlib_through_env_vars)

if __name__ == '__main__':
    unittest.main()
