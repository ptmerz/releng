"""
Interfacing with other systems (Gerrit, Jenkins)

This module should contain all code related to interacting with Gerrit
and as much as possible of the code related to interacting with the Jenkins job
configuration and passing information back to workflow Groovy scripts.
"""

import ast
import base64
import json
import os
import re
import traceback
import urllib.request, urllib.parse, urllib.error

from .common import AbortError, BuildError, ConfigurationError
from .common import Project, System
from . import utils

class RefSpec(object):

    """Wraps handling of refspecs used to check out projects."""

    @staticmethod
    def is_tarball_refspec(value):
        if value is None:
            return False
        return value.startswith('tarballs/')

    def __init__(self, value, remote_hash=None, executor=None):
        self._value = value
        self._remote = value
        if remote_hash:
            self._remote = remote_hash
        self.branch = None
        self.change_number = None
        self._tar_props = None
        if self.is_tarball_refspec(value):
            assert executor is not None
            prop_path = os.path.join(self._value, 'package-info.log')
            self._tar_props = utils.read_property_file(executor, prop_path)
            self._remote = self._tar_props['HEAD_HASH']
        elif value.startswith('refs/changes/'):
            self.change_number = value.split('/')[3]
        elif value.startswith('refs/heads/'):
            self.branch = value.split('/')[2]

    @property
    def is_no_op(self):
        """Whether this refspec is a magic no-op refspec used for testing."""
        return self._value == 'HEAD'

    @property
    def is_static(self):
        """Whether this refspec specifies a static commit at the remote side."""
        return self._value.startswith('refs/changes/')

    @property
    def fetch(self):
        """Git refspec used to fetch the corresponding commit."""
        return self._value

    @property
    def checkout(self):
        """Git refspec used for checkout after git fetch."""
        if self._remote == self._value:
            return 'FETCH_HEAD'
        return self._remote

    @property
    def is_tarball(self):
        return self._tar_props is not None

    @property
    def tarball_props(self):
        assert self.is_tarball
        return self._tar_props

    @property
    def tarball_path(self):
        assert self.is_tarball
        return os.path.join(self._value, self._tar_props['PACKAGE_FILE_NAME'])

    def __str__(self):
        """Value of this refspec in human-friendly format."""
        return self._value


class GerritChange(object):

    def __init__(self, json_data):
        self.project = Project.parse(json_data['project'])
        self.branch = json_data['branch']
        self.number = int(json_data['number'])
        self.title = json_data['subject']
        self.url = json_data['url']
        self.is_open = json_data['open']
        patchset = json_data['currentPatchSet']
        self.patchnumber = int(patchset['number'])
        self.refspec = RefSpec(patchset['ref'], patchset['revision'])


class GerritIntegration(object):

    """Provides access to Gerrit and Gerrit Trigger configuration.

    Methods encapsulate calls to Gerrit SSH commands (and possibly in the
    future, REST calls) and access to environment variables/build parameters
    set by Gerrit Trigger.
    """

    def __init__(self, factory, user=None):
        if user is None:
            user = 'jenkins'
        self._env = factory.env
        self._cmd_runner = factory.cmd_runner
        self._user = user
        self._is_windows = (factory.system == System.WINDOWS)

    def get_remote_hash(self, project, refspec):
        """Fetch hash of a refspec on the Gerrit server."""
        cmd = ['git', 'ls-remote', self.get_git_url(project), refspec.fetch]
        output = self._cmd_runner.check_output(cmd).split(None, 1)
        if len(output) < 2:
            return BuildError('failed to find refspec {0} for {1}'.format(refspec, project))
        return output[0].strip()

    def get_git_url(self, project):
        """Returns the URL for git to access the given project."""
        return 'ssh://{0}/{1}.git'.format(self._get_ssh_url(), project)

    def get_triggering_project(self):
        gerrit_project = self._env.get('GERRIT_PROJECT', None)
        if gerrit_project is None:
            return None
        return Project.parse(gerrit_project)

    def get_triggering_refspec(self):
        refspec = self._env.get('GERRIT_REFSPEC', None)
        if refspec is None:
            raise ConfigurationError('GERRIT_REFSPEC not set')
        return RefSpec(refspec)

    def get_triggering_branch(self):
        return self._env.get('GERRIT_BRANCH', None)

    def get_triggering_comment(self):
        text = self._env.get('GERRIT_EVENT_COMMENT_TEXT', None)
        if text:
            text = base64.b64decode(text)
            match = re.search(r'(?:^|\n\n)\[JENKINS\]\s*((?:.+\n)*(?:.+))(?:\n\n|\n?$)', text)
            if not match:
                return None
            return match.group(1).strip()
        text = self._env.get('MANUAL_COMMENT_TEXT', None)
        return text

    def query_change(self, query, expect_unique=True):
        if self._is_windows:
            return None
        cmd = self._get_ssh_query_cmd()
        cmd.extend(['--current-patch-set', '--', query])
        lines = self._cmd_runner.check_output(cmd).splitlines()
        if len(lines) < 2:
            raise BuildError(query + ' does not match any change')
        if len(lines) > 2 and expect_unique:
            raise BuildError(query + ' does not identify a unique change')
        return GerritChange(json.loads(lines[0]))

    def post_cross_verify_start(self, change, patchset):
        message = 'Cross-verify with {0} (patch set {1}) running at {2}'.format(
                self._env['GERRIT_CHANGE_URL'], self._env['GERRIT_PATCHSET_NUMBER'],
                self._env['BUILD_URL'])
        cmd = self._get_ssh_review_cmd(change, patchset, message)
        self._cmd_runner.check_call(cmd)

    def post_cross_verify_finish(self, change, patchset, build_messages):
        message = 'Cross-verify with {0} (patch set {1}) finished'.format(
                self._env['GERRIT_CHANGE_URL'], self._env['GERRIT_PATCHSET_NUMBER'])
        message += '\n\n' + '\n\n'.join(build_messages)
        cmd = self._get_ssh_review_cmd(change, patchset, message)
        self._cmd_runner.check_call(cmd)

    def _get_ssh_url(self):
        return self._user + '@gerrit.gromacs.org'

    def _get_ssh_gerrit_cmd(self, cmdname):
        return ['ssh', '-p', '29418', self._get_ssh_url(), 'gerrit', cmdname]

    def _get_ssh_query_cmd(self):
        return self._get_ssh_gerrit_cmd('query') + ['--format=JSON']

    def _get_ssh_review_cmd(self, change, patchset, message):
        changeref = '{0},{1}'.format(change, patchset)
        return self._get_ssh_gerrit_cmd('review') + [changeref, '-m', '"' + message + '"']


class ProjectInfo(object):
    """Information about a checked-out project.

    Attributes:
        project (str): Name of the git project (e.g. gromacs, regressiontests, releng)
        refspec (RefSpec): Refspec from which the project has been checked out.
        head_hash (str): SHA1 of HEAD.
        head_title (str): Title of the HEAD commit.
        remote_hash (str): SHA1 of the refspec at the remote repository.
    """

    def __init__(self, project, refspec):
        self.project = project
        self.branch = None
        self.refspec = refspec
        self.head_hash = None
        self.head_title = None
        self.remote_hash = None
        self.is_checked_out = False
        if refspec:
            self.set_branch(refspec.branch)
            if refspec.is_tarball:
                # TODO: Populate more useful information for print_project_info()
                self.head_hash = refspec.checkout
                self.head_title = 'From tarball'
                self.remote_hash = refspec.checkout

    def set_branch(self, branch):
        if self.branch is None:
            self.branch = branch

    def override_refspec(self, refspec):
        assert not self.is_checked_out
        self.refspec = refspec
        if refspec.branch:
            self.branch = refspec.branch

    def set_checked_out(self, workspace, gerrit):
        self.is_checked_out = True
        if self.is_tarball:
            return
        self.head_title, self.head_hash = workspace._get_git_commit_info(self.project, 'HEAD')
        if self.refspec.is_static:
            self.remote_hash = gerrit.get_remote_hash(self.project, self.refspec)
        else:
            self.remote_hash = self.head_hash

    def ensure_branch_loaded(self, gerrit):
        if not self.branch:
            self._load_from_gerrit(gerrit)

    def load_missing_info(self, workspace, gerrit):
        if self.is_tarball:
            return
        if not self.is_checked_out:
            self.head_hash = gerrit.get_remote_hash(self.project, self.refspec)
            self.remote_hash = self.head_hash
            self.head_title, dummy = workspace._get_git_commit_info(self.project, self.head_hash, allow_none=True)
        self._load_from_gerrit(gerrit)

    def _load_from_gerrit(self, gerrit):
        if self.head_title is None or self.branch is None:
            change = None
            if self.refspec.change_number:
                change = gerrit.query_change(self.refspec.change_number)
            elif self.head_hash:
                change = gerrit.query_change('commit:' + self.head_hash, expect_unique=False)
            if change:
                if self.head_title is None:
                    self.head_title = change.title
                if self.branch is None:
                    self.branch = change.branch

    @property
    def is_tarball(self):
        return self.refspec and self.refspec.is_tarball

    @property
    def build_branch_label(self):
        if self.branch is not None and self.branch.startswith('release-'):
            return self.branch[8:]
        return self.branch

    def has_correct_hash(self):
        assert self.is_checked_out
        return self.head_hash == self.remote_hash

    def to_dict(self):
        return {
                'project': self.project,
                'branch': self.branch,
                'build_branch_label': self.build_branch_label,
                'refspec': str(self.refspec),
                'hash': self.head_hash,
                'title': self.head_title,
                'refspec_env': '{0}_REFSPEC'.format(self.project.upper()),
                'hash_env': '{0}_HASH'.format(self.project.upper())
            }


class ProjectsManager(object):
    """Manages project refspecs and checkouts.

    This class is mainly responsible of managing the state related to project
    checkouts, including those checked out external to the Python code (in
    pipeline code, or in Jenkins job configuration).
    """

    def __init__(self, factory):
        self._cmd_runner = factory.cmd_runner
        self._env = factory.env
        self._executor = factory.executor
        self._gerrit = factory.gerrit
        self._workspace = factory.workspace
        self._projects = dict()
        self._branch = None
        self._init_projects()

    def _init_projects(self):
        """Determines the refspecs to be used, and initially checked out projects.

        If the build is triggered by Gerrit Trigger, then GERRIT_PROJECT
        environment variable exists, and the Jenkins build configuration needs
        to check out this project to properly integrate with different plugins.

        For other cases, CHECKOUT_PROJECT can also be used.
        """
        # The releng project is always checked out, since we are already
        # executing code from there...
        initial_projects = { Project.RELENG }
        for project in Project._values:
            refspec, exists = self._parse_refspec(project)
            if exists:
                self._projects[project] = ProjectInfo(project, refspec)

        checkout_project = self._parse_checkout_project()
        gerrit_project = self._gerrit.get_triggering_project()
        if gerrit_project is not None:
            project_info = self._projects[gerrit_project]
            gerrit_branch = self._gerrit.get_triggering_branch()
            project_info.set_branch(gerrit_branch)
            if gerrit_project != Project.RELENG:
                self._branch = gerrit_branch
            if not project_info.is_tarball:
                refspec = self._gerrit.get_triggering_refspec()
                project_info.override_refspec(refspec)
        if checkout_project is not None:
            refspec = self._env.get('CHECKOUT_REFSPEC', None)
            if refspec is not None:
                sha1 = self._env.get('{0}_HASH'.format(checkout_project.upper()), None)
                self._projects[checkout_project].override_refspec(RefSpec(refspec, sha1))
            initial_projects.add(checkout_project)

        self._resolve_missing_refspecs()

        for project in initial_projects:
            self._projects[project].set_checked_out(self._workspace, self._gerrit)

    def _parse_refspec(self, project):
        env_name = '{0}_REFSPEC'.format(project.upper())
        refspec = self._env.get(env_name, None)
        if refspec and refspec.lower() != 'auto':
            env_name = '{0}_HASH'.format(project.upper())
            sha1 = self._env.get(env_name, None)
            return RefSpec(refspec, sha1, executor=self._executor), True
        return None, refspec is not None

    def _parse_checkout_project(self):
        checkout_project = self._env.get('CHECKOUT_PROJECT', None)
        if checkout_project is None:
            return None
        return Project.parse(checkout_project)

    def _resolve_missing_refspecs(self):
        missing = set([p.project for p in self._projects.values() if not p.refspec])
        if not missing:
            return
        known = list([p for p in self._projects.values() if p.refspec and p.project != Project.RELENG])
        if not self._branch and known:
            assert len(known) == 1
            known[0].ensure_branch_loaded(self._gerrit)
            self._branch = known[0].branch
        if not self._branch:
            self._branch = 'master'
        refspec = RefSpec('refs/heads/' + self._branch)
        for project in missing:
            self._projects[project].override_refspec(refspec)

    def _verify_project(self, project, expect_checkout=False):
        if project not in self._projects:
            raise ConfigurationError(project.upper() + '_REFSPEC is not set')
        if expect_checkout and not self._projects[project].is_checked_out:
            raise ConfigurationError('accessing project {0} before checkout'.format(project))

    def init_workspace(self):
        projects = [p.project for p in self._projects.values() if p.is_checked_out]
        self._workspace._set_initial_checkouts(projects)

    def checkout_project(self, project):
        """Checks out the given project if not yet done for this build."""
        self._verify_project(project)
        project_info = self._projects[project]
        if project_info.is_checked_out:
            return
        refspec = project_info.refspec
        self._workspace._checkout_project(project, refspec)
        project_info.set_checked_out(self._workspace, self._gerrit)

    def get_project_info(self, project, expect_checkout=True):
        self._verify_project(project, expect_checkout)
        return self._projects[project]

    def print_project_info(self):
        """Prints information about the revisions used in this build."""
        console = self._executor.console
        print('-----------------------------------------------------------', file=console)
        print('Building using versions:', file=console)
        for project in Project._values:
            if project not in self._projects:
                continue
            project_info = self._projects[project]
            if not project_info.is_checked_out:
                continue
            correct_info = ''
            if not project_info.has_correct_hash():
                correct_info = ' (WRONG)'
            print('{0:16} {1:26} {2}{3}'.format(
                project_info.project + ':', project_info.refspec, project_info.head_hash, correct_info),
                file=console)
            if project_info.head_title:
                print('{0:19}{1}'.format('', project_info.head_title), file=console)
        print('-----------------------------------------------------------', file=console)

    def check_projects(self):
        """Checks that all checked-out projects are at correct revisions.

        In the past, there have been problems with not all projects getting
        correctly checked out.  It is unknown whether this was a Jenkins bug
        or something else, and whether the issue still exists.
        """
        console = self._executor.console
        all_correct = True
        for project_info in self._projects.values():
            if not project_info.is_checked_out:
                continue
            if not project_info.has_correct_hash():
                print('Checkout of {0} failed: HEAD is {1}, expected {2}'.format(
                    project, project_info.head_hash, project_info.remote_hash),
                    file=console)
                all_correct = False
        if not all_correct:
            raise BuildError('Checkout failed (Jenkins issue)')

    def get_build_revisions(self):
        projects = []
        for project in Project._values:
            if project not in self._projects:
                continue
            info = self._projects[project]
            info.load_missing_info(self._workspace, self._gerrit)
            projects.append(info)
        return [project.to_dict() for project in projects]

    def override_refspec(self, project, refspec):
        self._verify_project(project)
        self._projects[project].override_refspec(refspec)


class BuildParameters(object):
    """Access to build parameters."""

    def __init__(self, factory):
        self._env = factory.env

    def get(self, name, handler):
        """Gets the value of a build parameter/environment variable.

        If the parameter/environment variable is not set, None is returned.

        Args:
            name (str): Name of the parameter/environment variable to read.
            handler (function): Handler function that parses/converts the value.
        """
        value = self._env.get(name, None)
        if value is not None:
            value = handler(value)
        return value


class ParameterTypes(object):
    """Methods to pass to BuildParameters.get() for parsing build parameters."""

    @staticmethod
    def bool(value):
        """Parses a Jenkins boolean build parameter."""
        return value.lower() == 'true'

    @staticmethod
    def string(value):
        return value


class MatrixRunInfo(object):
    """Information retrieved from Jenkins about a single matrix configuration
    run results."""

    def __init__(self, opts, host, result, url):
        self.opts = opts
        self.host = host
        self.result = result
        self.url = url

    @property
    def is_success(self):
        return self.result == 'SUCCESS'

    @property
    def is_unstable(self):
        return self.result == 'UNSTABLE'

    @property
    def is_not_built(self):
        return self.result == 'NOT_BUILT'

    def to_dict(self):
        return {
                'opts': self.opts,
                'host': self.host,
                'result': self.result,
                'url': self.url
            }

class MatrixBuildInfo(object):
    """Information retrieved from Jenkins about matrix build results."""

    def __init__(self, result, json_runs_data):
        self.result = result
        self.runs = []
        for run_data in json_runs_data:
            result = run_data['result']
            url = run_data['url']
            options_parts = [x for x in url.split('/') if x.startswith("OPTIONS=")]
            assert len(options_parts) == 1
            opts = urllib.parse.unquote(options_parts[0][8:]).split()
            host = opts[-1].split(',')[0][5:]
            opts = opts[:-1]
            self.runs.append(MatrixRunInfo(opts, host, result, url))

    def merge_known_configs(self, configs):
        new_runs = []
        for config in configs:
            found = [x for x in self.runs if x.opts == config.opts]
            if not found:
                new_runs.append(MatrixRunInfo(config.opts, config.host, 'NOT_BUILT', None))
            else:
                assert len(found) == 1
                new_runs.append(found[0])
        self.runs = new_runs

    @property
    def is_success(self):
        return self.result == 'SUCCESS'

    @property
    def is_aborted(self):
        return self.result == 'ABORTED'


class JenkinsIntegration(object):
    """Access to Jenkins specifics such as build parameters."""

    def __init__(self, factory):
        self.workspace_root = factory.env['WORKSPACE']
        self.node_name = factory.env.get('NODE_NAME', None)
        if not self.node_name:
            self.node_name = 'unknown'
        self.params = BuildParameters(factory)

    def query_matrix_build(self, url):
        """Queries basic information about a matrix build from Jenkins REST API.

        Args:
            url (str): Base absolute URL of the Jenkins build to query.
        """
        data = self._query_build(url, 'result,number,runs[number,url]')
        # For some reason, Jenkins returns runs also for previous builds in case
        # those are no longer part of the current matrix.  Those that actually
        # belong to the queried run can be identified by matching build numbers.
        run_urls = [x['url'] for x in data['runs'] if x['number'] == data['number']]
        runs_data = []
        for url in run_urls:
            result = self._query_build(url, 'url,result')
            runs_data.append(result)
        return MatrixBuildInfo(data['result'], runs_data)

    def _query_build(self, url, tree):
        query_url = '{0}/api/python?tree={1}'.format(url, tree)
        return ast.literal_eval(urllib.request.urlopen(query_url).read())


class StatusReporter(object):
    """Handles tracking and reporting of failures during the build.

    Attributes:
        failed (bool): Whether the build has already failed.
    """

    def __init__(self, factory, tracebacks=True):
        self._status_file = factory.env.get('STATUS_FILE', 'logs/unsuccessful-reason.log')
        self._propagate_failure = not bool(factory.env.get('NO_PROPAGATE_FAILURE', False))
        self._executor = factory.executor
        self._executor.remove_path(self._status_file)
        self._workspace = factory.workspace
        if not os.path.isabs(self._status_file):
            self._status_file = os.path.join(self._workspace.root, self._status_file)
        self.failed = False
        self._aborted = False
        self._unsuccessful_reason = []
        self.return_value = None
        self._tracebacks = tracebacks

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, tb):
        returncode = 1
        if exc_type is not None:
            console = self._executor.console
            if not self._tracebacks:
                tb = None
            if issubclass(exc_type, AbortError):
                self._aborted = True
                returncode = exc_value.returncode
            elif issubclass(exc_type, ConfigurationError):
                traceback.print_exception(exc_type, exc_value, tb, file=console)
                self.mark_failed('Jenkins configuration error: ' + str(exc_value))
            elif issubclass(exc_type, BuildError):
                traceback.print_exception(exc_type, exc_value, tb, file=console)
                self.mark_failed(str(exc_value))
            else:
                lines = traceback.format_exception(exc_type, exc_value, tb)
                lines = [x.rstrip() for x in lines]
                self._unsuccessful_reason.extend(lines)
                self._report_on_exception()
                return False
        self._report()
        # Currently, we do not propagate even the aborted return value when
        # not requested to.  This means that the parent workflow build may
        # have a chance to write a summary to the build summary page before
        # exiting.  Not sure what Jenkins does if the workflow build takes
        # a long time afterwards to finish, though...
        if (self._aborted or self.failed) and self._propagate_failure:
            self._executor.exit(returncode)
        return True

    def mark_failed(self, reason):
        """Marks the build failed.

        Args:
            reason (str): Reason printed to the build log for the failure.
        """
        self.failed = True
        self._unsuccessful_reason.append(reason)

    def mark_unstable(self, reason, details=None):
        """Marks the build unstable.

        Args:
            reason (str): Reason printed to the build console log for the failure.
            details (Optional[List[str]]): Reason(s) reported back to Gerrit.
                If not provided, reason is used.
        """
        print('FAILED: ' + reason, file=self._executor.console)
        if details is None:
            self._unsuccessful_reason.append(reason)
        else:
            self._unsuccessful_reason.extend(details)

    def _report_on_exception(self):
        console = self._executor.console
        try:
            self._report(to_console=False)
        except:
            traceback.print_exc(file=console)

    def _report(self, to_console=True):
        """Reports possible failures at the end of the build."""
        result = 'SUCCESS'
        reason = None
        if self._aborted:
            result = 'ABORTED'
        elif self.failed:
            result = 'FAILURE'
        elif self._unsuccessful_reason:
            result = 'UNSTABLE'
        if not self._aborted and self._unsuccessful_reason:
            reason = '\n'.join(self._unsuccessful_reason)
        if reason and to_console:
            console = self._executor.console
            print('Build FAILED:', file=console)
            for line in self._unsuccessful_reason:
                print('  ' + line, file=console)
        contents = None
        ext = os.path.splitext(self._status_file)[1]
        if ext == '.json':
            output = {
                    'result': result,
                    'reason': reason
                }
            if self.return_value:
                output['return_value'] = self.return_value
            contents = json.dumps(output, indent=2)
        elif reason:
            contents = reason + '\n'
        if contents:
            self._executor.ensure_dir_exists(os.path.dirname(self._status_file))
            self._executor.write_file(self._status_file, contents)
        if self.failed:
            assert self._unsuccessful_reason, "Failed build did not produce an unsuccessful reason"
