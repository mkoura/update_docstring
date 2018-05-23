# -*- coding: utf-8 -*-
# pylint: disable=missing-docstring

from __future__ import absolute_import, unicode_literals

import argparse
import io
import logging
import os
import re
import textwrap

from polarion_docstrings import polarion_fields as pf
from polarion_pytest.svn_polarion import PolarionTestcases
from polarion_pytest.testinfo import CurrentTest, CurrentTier


# pylint: disable=invalid-name
logger = logging.getLogger(__name__)


MANUAL_TESTS_FILE = 'cfme/tests/test_manual.py'


class TestcasesException(Exception):
    pass


class TestsTransform(object):

    DEFAULT_DATA = {key: None for key in pf.REQUIRED_FIELDS}

    def __init__(self):
        self.manual_tests_seen = []

    @staticmethod
    def _copy_string(string, to_string):
        """Copies string to string representing changed file."""
        if string:
            to_string.append(string)

    @staticmethod
    def append_tier(polarion_data, line):
        caselevel = polarion_data.get('caselevel', 'component')
        if not polarion_data:
            return line
        # if default value, no need to specify explicitly
        if pf.POLARION_FIELDS['caselevel'] == caselevel:
            return line
        tier = pf.CASELEVELS[caselevel]
        method_indent = len(line) - len(line.lstrip(' '))
        return '{}@pytest.mark.tier({})\n{}'.format(method_indent * ' ', tier, line)

    @staticmethod
    def get_testcase_data(testcase_name, active_testcases):
        try:
            return active_testcases.get_by_name(testcase_name, automated=True)
        except KeyError:
            return None

    @staticmethod
    def filter_testcase_fields(testcase_data):
        if not testcase_data:
            return None

        polarion_data = {}
        for field, default_value in pf.POLARION_FIELDS.items():
            polarion_value = testcase_data.get(field)
            if field in pf.REQUIRED_FIELDS and not polarion_value:
                polarion_value = None
            if field not in pf.REQUIRED_FIELDS and not polarion_value:
                continue
            if polarion_value != default_value:
                polarion_data[field] = polarion_value
        return polarion_data

    def get_polarion_data(self, testcase_name, active_testcases):
        testcase_data = self.get_testcase_data(testcase_name, active_testcases)
        return self.filter_testcase_fields(testcase_data)

    @staticmethod
    def _format_steps(steps):
        if len(steps) == 1 and not steps[0]:
            return []

        new_steps = []
        indent = 4 * ' '

        for index, step in enumerate(steps, 1):
            step = _sanitize_string(step).strip() if step else ''

            numbered = re.match(r'[0-9]+[.)]? ?', step)

            if numbered:
                step_num = ''
            elif step:
                step_num = '{}. '.format(index)
            else:
                step_num = '{}.'.format(index)

            # wrap line if too long
            steps = []
            if len(step) > 80:
                steps = textwrap.wrap(step, width=60)
                step = steps.pop(0)

            step_str = '{}{}{}'.format(indent, step_num, step)
            new_steps.append(step_str)

            wrap_indent = len(step_num) if step_num else len(numbered.group(0))
            wrap_indent = '{}{}'.format(indent, wrap_indent * ' ')
            for val in steps:
                new_steps.append('{}{}'.format(wrap_indent, val))

        return new_steps

    def _format_polarion_data(self, polarion_data):
        data_list = []
        steps = []
        results = []
        ommit_keys = ['caseautomation', 'caselevel', 'description', 'testSteps', 'expectedResults']

        # "caseautomation" is not "automated" when it's present
        if 'caseautomation' not in polarion_data:
            for key in pf.MANUAL_ONLY_FIELDS:
                ommit_keys.append(key)

        title = polarion_data.get('title', '')
        if 'test_' in title and len(title) <= 85:
            del polarion_data['title']

        if polarion_data.get('testSteps'):
            new_steps = self._format_steps(polarion_data['testSteps'])
            if new_steps:
                steps = ['Steps:']
                steps.extend(new_steps)

        if polarion_data.get('expectedResults'):
            new_results = self._format_steps(polarion_data['expectedResults'])
            if new_results:
                results = ['Results:']
                results.extend(new_results)

        casecomponent = polarion_data.get('casecomponent')
        if casecomponent and casecomponent in pf.CASECOMPONENT_MAP:
            casecomponent = pf.CASECOMPONENT_MAP[casecomponent]
            polarion_data['casecomponent'] = casecomponent

        for key in sorted(polarion_data):
            if key in ommit_keys:
                continue
            value = polarion_data[key]
            if value:
                if key not in pf.VALID_VALUES:
                    value = _sanitize_string(value)
                value = value.strip()
            else:
                value = ''
            key_indent = (len(key) + 2) * ' '

            # wrap line if too long
            values = []
            if len(value) > 80:
                values = textwrap.wrap(value, width=60)
                value = values.pop(0)

            data_list.append('{}: {}'.format(key, value or None))
            for val in values:
                data_list.append('{}{}'.format(key_indent, val))

        data_list.extend(steps)
        data_list.extend(results)

        return data_list

    def get_polarion_docstring(self, polarion_data, indent):
        """Generates polarion decstring."""
        if not polarion_data:
            return ''

        indent = indent * ' '
        polarion_docstrings = []
        polarion_docstrings.append('{}Polarion:'.format(indent))
        pol_indent = '{}{}'.format(indent, 4 * ' ')

        lines = self._format_polarion_data(polarion_data)
        for line in lines:
            polarion_docstrings.append('{}{}'.format(pol_indent, line))
        return '\n'.join(polarion_docstrings)

    def append_to_docstring(self, tests_info, polarion_data):
        """Appends content to existing docstring."""
        if not (tests_info.docstring_data or polarion_data):
            return ''

        if tests_info.test_class:
            indent = 8
        else:
            indent = 4
        indent_str = indent * ' '

        if tests_info.docstring_data:
            new_docstring = ''.join(tests_info.docstring_data)
        else:
            new_docstring = '{}"""'.format(indent_str)

        polarion_docstring = self.get_polarion_docstring(polarion_data, indent)
        new_docstring = '{}\n{}\n{}"""\n'.format(new_docstring, polarion_docstring, indent_str)
        return new_docstring

    def process_testfile(self, pyinput, active_testcases, newfile):
        """Adds Polarion data to docstrings."""
        tiers_info = CurrentTier()
        tests_info = CurrentTest()
        line = pyinput.readline()
        modified = False
        polarion_data = None
        last_test = tests_info.test_name

        while line:
            modified_line = tiers_info.process_line(line)
            modified_line = tests_info.process_line(modified_line)
            if tests_info.line_is_in_docstring:
                modified_line = None
            if last_test != tests_info.test_name:
                last_test = tests_info.test_name
                polarion_data = self.get_polarion_data(tests_info.test_name, active_testcases)
                if polarion_data and 'caseautomation' in polarion_data:
                    self.manual_tests_seen.append(tests_info.test_name)
            if tiers_info.tier_missing and polarion_data:
                modified_line = self.append_tier(polarion_data, modified_line)
                modified = True
            if tests_info.docstring_end:
                modified_line = '{}{}'.format(
                    self.append_to_docstring(tests_info, polarion_data or self.DEFAULT_DATA),
                    modified_line or ''
                )
                modified = True

            self._copy_string(modified_line, newfile)
            line = pyinput.readline()

        return modified


def _sanitize_string(string):
    cdata = re.search(r'<!\[CDATA\[(.+)\]\]>', string)
    if cdata:
        string = cdata.group(1)
    string = re.sub('<br ?/?>', r'\n', string)
    string = re.sub('<[^>]+>', '', string)
    string = re.sub(' *\n', '\n', string)
    string = re.sub(r'(\n)+', r'\n', string)
    string = (string
              .replace('&npsp;', ' ')
              .replace('&gt;', '>')
              .replace('&lt;', '<')
              .replace('&quot;', '"')
              .replace('&amp;', '&')
              .replace('&#39;', '"')
              .replace(u'\xa0', u' '))
    return string


def get_active_testcases(repo_dir):
    """Gets active testcases in Polarion."""
    polarion_testcases = PolarionTestcases(repo_dir)
    try:
        polarion_testcases.load_active_testcases()
    except Exception as err:
        raise TestcasesException(
            'Failed to load testcases from SVN repo {}: {}'.format(repo_dir, err))
    if not polarion_testcases:
        raise TestcasesException(
            'No testcases loaded from SVN repo {}'.format(repo_dir))
    polarion_testcases.strip_parameters()
    return polarion_testcases


def get_test_files():
    """Finds all test files."""
    for root, __, files in os.walk('./'):
        for _file in files:
            if '.py' in _file and 'test_' in _file:
                yield '{}/{}'.format(root, _file)


def overwrite_file(pyfile, string):
    """Overwrite old file with new content."""
    if not string:
        return
    with io.open(pyfile, 'w', encoding='utf-8') as pyout:
        pyout.write(string)
        pyout.truncate()


def gen_modified_content(pyfile, active_testcases, tests_transform):
    """Generates the modified test file."""
    newfile = []
    modified = False

    with io.open(pyfile, encoding='utf-8') as pyinput:
        modified = tests_transform.process_testfile(pyinput, active_testcases, newfile)

    if modified:
        newfile_str = ''.join(newfile)
        return newfile_str
    return None


def manual_tests_header():
    return [
        '# -*- coding: utf-8 -*-',
        '# pylint: skip-file',
        '"""Manual tests"""\n\n'
        'import pytest\n\n']


def add_manual_test(test_name, polarion_data, tests_transform):
    lines = ['@pytest.mark.manual']
    indent = 4 * ' '

    new_test_name = polarion_data.get('title') or test_name
    new_test_name = (new_test_name
                     .replace(' ', '_')
                     .replace(':', '_')
                     .replace('/', '_')
                     .replace('-', '_')
                     .replace('(', '_')
                     .replace(')', '_')
                     .replace('[', '_')
                     .replace(']', '_')
                     .strip()
                     .strip('_')
                     .lower())
    new_test_name = re.sub('_+', '_', new_test_name)
    new_test_name = re.sub('[^a-z0-9_]', '', new_test_name)
    new_test_name = new_test_name[:85]
    if 'test_' not in new_test_name:
        new_test_name = 'test_{}'.format(new_test_name)

    test_def = tests_transform.append_tier(polarion_data, 'def {}():'.format(new_test_name))
    lines.append(test_def)

    description = polarion_data.get('description') or []
    new_description = ['{}{}'.format(indent, line) for line in description if line]
    if new_description:
        new_description.append('\n')
        new_description = '\n'.join(new_description)
    else:
        new_description = ''

    docstring = tests_transform.get_polarion_docstring(polarion_data, 4)
    lines.append('{ind}"""\n{desc}{doc}\n{ind}"""\n\n'.format(
        ind=indent, desc=new_description, doc=docstring))

    return lines


def sanitize_description(polarion_data):
    description = polarion_data.get('description')
    if not description:
        return

    new_description = _sanitize_string(description)
    if new_description:
        desc_lines = new_description.split('\n')
        new_lines = []
        for line in desc_lines:
            new_lines.extend(textwrap.wrap(line.strip()))
        polarion_data['description'] = new_lines
    else:
        del polarion_data['description']


def _get_manual_polarion_data(testcase_name, active_testcases, tests_transform):
    multi_polarion_data = []
    for testcase_id in active_testcases.available_testcases[testcase_name]:
        testcase_data = active_testcases.wi_cache[testcase_id] or {}
        if testcase_data.get('caseautomation') != 'automated':
            # TODO: remove once ready
            testcase_data['test_id'] = testcase_id
            multi_polarion_data.append(tests_transform.filter_testcase_fields(testcase_data))
    return multi_polarion_data


def gen_manual_testcases(active_testcases, tests_transform):
    all_lines = manual_tests_header()
    manual_testcases = active_testcases.get_manual_testcases()
    to_process = set(manual_testcases) - set(tests_transform.manual_tests_seen)

    for test_name in to_process:
        multi_polarion_data = _get_manual_polarion_data(
            test_name, active_testcases, tests_transform)
        if not multi_polarion_data:
            logger.error('Failed to get data for test `%s`', test_name)
            continue
        for polarion_data in multi_polarion_data:
            sanitize_description(polarion_data)
            test_lines = add_manual_test(test_name, polarion_data, tests_transform)
            all_lines.extend(test_lines)

    return all_lines


def get_args(args=None):
    """Get command line arguments."""
    parser = argparse.ArgumentParser(description='cfme-update-docstrings')
    parser.add_argument('-r', '--repo_dir', metavar='SVN_REPO',
                        help='Path to SVN repo with Polarion project')
    parser.add_argument('--log-level',
                        help='Set logging to specified level')
    return parser.parse_args(args)


def init_log(log_level):
    """Initializes logging."""
    log_level = log_level or 'INFO'
    logging.basicConfig(
        format='%(name)s:%(levelname)s:%(message)s',
        level=getattr(logging, log_level.upper(), logging.INFO)
    )


def main(args=None):
    args = get_args(args)
    init_log(args.log_level)

    try:
        os.remove(MANUAL_TESTS_FILE)
    except OSError:
        pass

    active_testcases = get_active_testcases(args.repo_dir)
    tests_transform = TestsTransform()

    for test_file in get_test_files():
        new_content = gen_modified_content(test_file, active_testcases, tests_transform)
        overwrite_file(test_file, new_content)

    manual_tests_lines = gen_manual_testcases(active_testcases, tests_transform)
    manual_tests = '{}\n'.format('\n'.join(manual_tests_lines).rstrip())
    overwrite_file(MANUAL_TESTS_FILE, manual_tests)

    return 0
