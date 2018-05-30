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
from polarion_pytest.requirements_mapping import REQUIREMENTS_MAP
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
    def get_tier(polarion_data):
        caselevel = polarion_data.get('caselevel', 'component')
        if not polarion_data:
            return None
        # if default value, no need to specify explicitly
        if pf.POLARION_FIELDS['caselevel'] == caselevel:
            return None
        return pf.CASELEVELS[caselevel]

    def get_tier_annotation(self, polarion_data):
        tier = self.get_tier(polarion_data)
        if not tier:
            return None
        return '@pytest.mark.tier({})'.format(tier)

    def append_tier(self, polarion_data, line):
        tier_annotation = self.get_tier_annotation(polarion_data)
        if not tier_annotation:
            return line
        method_indent = len(line) - len(line.lstrip(' '))
        return '{}{}\n{}'.format(method_indent * ' ', tier_annotation, line)

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

    def _get_formatted_steps(self, polarion_data):
        steps = []
        results = []

        if polarion_data.get('testSteps'):
            new_steps = self._format_steps(polarion_data['testSteps'])
            if new_steps:
                steps = ['testSteps:']
                steps.extend(new_steps)

        if polarion_data.get('expectedResults'):
            new_results = self._format_steps(polarion_data['expectedResults'])
            if new_results:
                results = ['expectedResults:']
                results.extend(new_results)

        return steps, results

    @staticmethod
    def _transform_polarion_data(polarion_data):
        title = polarion_data.get('title', '')
        if 'test_' in title and len(title) <= 85:
            del polarion_data['title']

        if polarion_data.get('linkedWorkItems'):
            new_linked = ', '.join(polarion_data.get('linkedWorkItems'))
            polarion_data['linkedWorkItems'] = new_linked

        casecomponent = polarion_data.get('casecomponent')
        if casecomponent and casecomponent in pf.CASECOMPONENT_MAP:
            casecomponent = pf.CASECOMPONENT_MAP[casecomponent]
            polarion_data['casecomponent'] = casecomponent

        setup = polarion_data.get('setup')
        if setup:
            new_setup = _sanitize_paragraph(setup)
            polarion_data['setup'] = new_setup

        teardown = polarion_data.get('teardown')
        if teardown:
            new_teardown = _sanitize_paragraph(teardown)
            polarion_data['teardown'] = new_teardown

    @staticmethod
    def _wrap_values(polarion_data):
        ommit_keys = {
            'casecomponent',
            'setup',
            'teardown',
            'description',
            'testSteps',
            'expectedResults',
            'linkedWorkItems',
        }

        for key in set(polarion_data) - ommit_keys:
            line = polarion_data[key]

            if not line or isinstance(line, list):
                continue

            if key not in pf.VALID_VALUES:
                line = _sanitize_string(line)
            line = line.strip()

            # wrap line if too long
            if len(line) > 80:
                line = textwrap.wrap(line, width=60)
            elif '\n' in line:
                line = line.replace('\n', ' ')
                line = re.sub(' +', ' ', line)

            polarion_data[key] = line

    def format_polarion_data(self, polarion_data):
        data_list = []
        ommit_keys = {
            'caseautomation',
            'caselevel',
            'description',
            'testSteps',
            'expectedResults',
            'work_item_id',
        }

        # "caseautomation" is not "automated" when it's present
        if 'caseautomation' not in polarion_data:
            for key in pf.MANUAL_ONLY_FIELDS:
                ommit_keys.add(key)

        steps, results = self._get_formatted_steps(polarion_data)
        self._transform_polarion_data(polarion_data)
        self._wrap_values(polarion_data)

        for key in sorted(set(polarion_data) - ommit_keys):
            lines = polarion_data[key] or []
            first_line = None
            if lines:
                if isinstance(lines, list):
                    first_line = lines[0]
                    lines = lines[1:]
                else:
                    first_line = lines
                    lines = []

            key_indent = (len(key) + 2) * ' '

            if first_line or key in pf.REQUIRED_FIELDS:
                data_list.append('{}: {}'.format(key, first_line or None))
            for val in lines:
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

        lines = self.format_polarion_data(polarion_data)
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
    string = (string
              .replace('&npsp;', ' ')
              .replace('&gt;', '>')
              .replace('&lt;', '<')
              .replace('&quot;', '"')
              .replace('&amp;', '&')
              .replace('&#39;', '"')
              .replace('&#10;', '\n')
              .replace(u'\xa0', u' '))
    string = re.sub(r'(\n)+', r'\n', string)
    return string


def _sanitize_paragraph(paragraph):
    if not paragraph:
        return None
    new_paragraph = _sanitize_string(paragraph)
    if not new_paragraph:
        return None

    desc_lines = new_paragraph.split('\n')
    new_lines = []
    for line in desc_lines:
        new_lines.extend(textwrap.wrap(line.strip()))
    return new_lines


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
    for root, __, files in os.walk('./cfme/tests/'):
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


def get_requirements_db():
    req_db = {}
    for req_name, req_ids in REQUIREMENTS_MAP.items():
        for req_id in req_ids:
            req_db[req_id] = req_name
    return req_db


def get_requirement_annotation(polarion_data, req_db):
    req = polarion_data.get('linkedWorkItems')
    if not req:
        return None

    del polarion_data['linkedWorkItems']
    req = req.pop()
    req_name = req_db.get(req)
    if not req_name:
        return None

    return '@test_requirements.{}'.format(req_name)


def manual_tests_header():
    return [
        '# -*- coding: utf-8 -*-',
        '# pylint: skip-file',
        '"""Manual tests"""\n',
        'import pytest\n',
        'from cfme import test_requirements\n\n',
    ]


def add_manual_test(test_name, polarion_data, tests_transform, req_db):
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

    req_annotation = get_requirement_annotation(polarion_data, req_db)
    if req_annotation:
        lines.append(req_annotation)
    tier_annotation = tests_transform.get_tier_annotation(polarion_data)
    if tier_annotation:
        lines.append(tier_annotation)
    lines.append('def {}():'.format(new_test_name))

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

    new_description = _sanitize_paragraph(description)
    if new_description:
        polarion_data['description'] = new_description
    else:
        del polarion_data['description']


def _get_manual_polarion_data(testcase_name, active_testcases, tests_transform):
    multi_polarion_data = []
    for testcase_id in active_testcases.available_testcases[testcase_name]:
        testcase_data = active_testcases.wi_cache[testcase_id] or {}
        if testcase_data.get('caseautomation') != 'automated':
            multi_polarion_data.append(tests_transform.filter_testcase_fields(testcase_data))
    return multi_polarion_data


def gen_manual_testcases(active_testcases, tests_transform):
    all_lines = manual_tests_header()
    manual_testcases = active_testcases.get_manual_testcases()
    to_process = set(manual_testcases) - set(tests_transform.manual_tests_seen)
    if not to_process:
        return all_lines

    req_db = get_requirements_db()

    for test_name in to_process:
        multi_polarion_data = _get_manual_polarion_data(
            test_name, active_testcases, tests_transform)
        if not multi_polarion_data:
            logger.error('Failed to get data for test `%s`', test_name)
            continue
        for polarion_data in multi_polarion_data:
            sanitize_description(polarion_data)
            test_lines = add_manual_test(test_name, polarion_data, tests_transform, req_db)
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
