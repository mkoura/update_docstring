# -*- coding: utf-8 -*-
# pylint: disable=missing-docstring

import logging
import os
import re

from collections import defaultdict

from lxml import etree


# pylint: disable=invalid-name
logger = logging.getLogger(__name__)


# pylint: disable=too-few-public-methods
class InvalidObject(object):
    """Item not present or it's not testcase."""
    pass


class WorkItemCache(object):
    """Cache of Polarion workitems."""
    def __init__(self, repo_dir):
        self.repo_dir = repo_dir
        self.test_case_dir = os.path.join(self.repo_dir, 'tracker/workitems/')
        self._cache = defaultdict(dict)

    @staticmethod
    def get_path(num):
        """Gets a path from the workitem number.

        For example: 31942 will return 30000-39999/31000-31999/31900-31999
        """
        num = int(num)
        dig_len = len(str(num))
        paths = []
        for i in range(dig_len - 2):
            divisor = 10 ** (dig_len - i - 1)
            paths.append(
                '{}-{}'.format((num / divisor) * divisor, (((num / divisor) + 1) * divisor) - 1))
        return '/'.join(paths)

    def get_tree(self, work_item_id):
        """Gets XML tree of the workitem."""
        try:
            __, tcid = work_item_id.split('-')
        except ValueError:
            logger.warning('Couldn\'t load workitem %s, bad format', work_item_id)
            self._cache[work_item_id] = InvalidObject()
            return None

        path = os.path.join(
            self.test_case_dir, self.get_path(tcid), work_item_id, 'workitem.xml')
        try:
            tree = etree.parse(path)
        # pylint: disable=broad-except
        except Exception:
            logger.warning('Couldn\'t load workitem %s', work_item_id)
            self._cache[work_item_id] = InvalidObject()
            return None
        return tree

    @staticmethod
    def _get_steps(item):
        steps = []
        expected_results = []

        steps_list = item.xpath('.//item[@id = "steps"]')
        if not steps_list:
            return steps, expected_results

        steps_list = steps_list[0]
        steps_items = steps_list.xpath('.//item[@text-type]')
        for index, rec in enumerate(steps_items):
            if index % 2:
                expected_results.append(rec.text)
            else:
                steps.append(rec.text)
        return steps, expected_results

    def __getitem__(self, work_item_id):
        if work_item_id in self._cache:
            return self._cache[work_item_id]
        elif isinstance(self._cache[work_item_id], InvalidObject):
            return None

        tree = self.get_tree(work_item_id)
        if not tree:
            return None

        for item in tree.xpath('/work-item/field'):
            if item.attrib['id'] == 'testSteps':
                steps, results = self._get_steps(item)
                self._cache[work_item_id]['testSteps'] = steps
                self._cache[work_item_id]['expectedResults'] = results
            else:
                self._cache[work_item_id][item.attrib['id']] = item.text

        if self._cache[work_item_id]['type'] != 'testcase':
            self._cache[work_item_id] = InvalidObject()
            return None

        if 'assignee' not in self._cache[work_item_id]:
            self._cache[work_item_id]['assignee'] = ''
        if 'title' not in self._cache[work_item_id]:
            logger.debug('Workitem %s has no title', work_item_id)

        return self._cache[work_item_id]


class PolarionTestcases(object):
    """Loads and access Polarion testcases."""

    TEST_PARAM = re.compile(r'\[.*\]')

    def __init__(self, repo_dir):
        self.repo_dir = os.path.expanduser(repo_dir)
        self.wi_cache = WorkItemCache(self.repo_dir)
        self.available_testcases = {}

    def load_active_testcases(self):
        """Creates dict of all active testcase's names and ids."""
        cases = {}
        for item in os.walk(self.wi_cache.test_case_dir):
            if 'workitem.xml' not in item[2]:
                continue
            case_id = os.path.split(item[0])[-1]
            if not (case_id and '*' not in case_id):
                continue
            item_cache = self.wi_cache[case_id]
            if not item_cache:
                continue
            case_status = item_cache.get('status')
            if not case_status or case_status == 'inactive':
                continue
            case_title = item_cache.get('title')
            if not case_title:
                continue
            try:
                cases[case_title].append(case_id)
            except KeyError:
                cases[case_title] = [case_id]

        self.available_testcases = cases

    def strip_parameters(self):
        filtered_testcases = {}
        for case_title, case_ids in self.available_testcases.items():
            param_strip = self.TEST_PARAM.sub('', case_title)
            try:
                filtered_testcases[param_strip].extend(case_ids)
            except KeyError:
                filtered_testcases[param_strip] = case_ids
        self.available_testcases = filtered_testcases

    def get_manual_testcases(self):
        manual_testcases = {}
        for case_title, case_ids in self.available_testcases.items():
            for case_id in case_ids:
                case = self.wi_cache[case_id]
                caseautomation = case.get('caseautomation')
                if caseautomation == 'automated':
                    continue
                try:
                    manual_testcases[case_title].append(case_id)
                except KeyError:
                    manual_testcases[case_title] = [case_id]
        return manual_testcases

    @staticmethod
    def _check_automation(case, should_be_automated):
        caseautomation = case.get('caseautomation')
        if should_be_automated:
            return caseautomation == 'automated'
        return caseautomation != 'automated'

    def get_by_name(self, testcase_name, automated=None):
        """Gets testcase by it's name."""
        if automated is None:
            testcase_id = self.available_testcases[testcase_name][0]
            return self.wi_cache[testcase_id]

        for case_id in self.available_testcases[testcase_name]:
            case = self.wi_cache[case_id]
            if self._check_automation(case, automated):
                return case
        return None

    def get_by_id(self, testcase_id):
        """Gets testcase by it's id."""
        return self.wi_cache[testcase_id]

    def __iter__(self):
        return iter(self.available_testcases)

    def __len__(self):
        return len(self.available_testcases)

    def __contains__(self, item):
        return item in self.available_testcases

    def __repr__(self):
        return '<Testcases {}>'.format(self.available_testcases)
