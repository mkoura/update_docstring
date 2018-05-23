# -*- coding: utf-8 -*-
# pylint: disable=missing-docstring

from __future__ import absolute_import, unicode_literals

import re


class CurrentTier(object):
    TIER_MOD = re.compile(r'pytest.mark.tier\(([1-3])\)')
    TIER_DEC = re.compile(r'@pytest.mark.tier\(([1-3])\)')

    def __init__(self):
        self.tier_missing = False
        self._module = None
        self._class = None
        self._method = None
        self._method_indent = 0
        self._tier_pending = None

    def process_line(self, line):
        """Sets correct tier if found."""
        self.tier_missing = False
        tier_found = self.TIER_DEC.search(line)
        if tier_found:
            self._tier_pending = tier_found.group(1)
            return line
        tier_found = self.TIER_MOD.search(line)
        if tier_found:
            self._module = tier_found.group(1)
            return line

        if 'class Test' in line:
            self._class = None
            if self._tier_pending:
                self._class = self._tier_pending
                self._tier_pending = None
        elif 'def test_' in line:
            self._method = None
            if self._tier_pending:
                self._method = self._tier_pending
                self._tier_pending = None
            self._method_indent = len(line) - len(line.lstrip(' '))
            if self._method_indent == 0:
                self._class = None
            if not (self._method or self._class or self._module):
                self.tier_missing = True

        return line


class CurrentTest(object):
    CLASS_NAME = re.compile(r' *class (Test[^(:]*)[(:]')
    TEST_NAME = re.compile(r' *def (test_[^(]*)\(')
    TEST_DEF_END = re.compile(r'.*\): *$')
    DOCSTRING_START = re.compile(r' *"""')
    DOCSTRING_SAME_END = re.compile(r' *[^ ].+""" *$')
    DOCSTRING_END = re.compile(r' *""" *$')

    def __init__(self):
        self.test_class = None
        self.test_name = None
        self.docstring_data = []
        self.docstring_begin = False
        self.docstring_end = False
        self.line_is_in_docstring = False
        self._test_def_now = False
        self._docstring_next = False
        self._docstring_now = False

    def _handle_same_line_end(self, line):
        line = line.rstrip()
        line = line.rstrip('"""')
        line = line.rstrip()
        self.docstring_data.append('{}\n'.format(line))
        self.docstring_end = True
        self.line_is_in_docstring = True
        self._docstring_now = False

    def _handle_class(self, line):
        class_name = self.CLASS_NAME.match(line)
        if not class_name:
            return

        self.test_class = class_name.group(1)

    def _handle_test(self, line):
        test_name = self.TEST_NAME.match(line)
        if not test_name:
            return

        self.test_name = test_name.group(1)
        curr_indent = len(line) - len(line.lstrip(' '))
        if curr_indent > 0:
            self.test_name = '{}.{}'.format(self.test_class, self.test_name)
        else:
            self.test_class = None

        self._test_def_now = True
        self.docstring_data = []

        if self.TEST_DEF_END.match(line):
            self._test_def_now = False
            self._docstring_next = True

    def process_line(self, line):
        self.docstring_begin = False
        self.docstring_end = False
        self.line_is_in_docstring = False
        if 'class Test' in line:
            self._handle_class(line)
        elif 'def test_' in line:
            self._handle_test(line)
        elif self._test_def_now and self.TEST_DEF_END.match(line):
            self._docstring_next = True
            self._test_def_now = False
        elif self._docstring_next:
            if not line.strip():
                # empty line, continue to next
                return line
            self._docstring_next = False
            if not self.DOCSTRING_START.match(line):
                # function without docstring
                self.docstring_end = True
                return line
            self.docstring_data = []
            if self.DOCSTRING_SAME_END.match(line):
                self._handle_same_line_end(line)
                return line
            self.docstring_begin = True
            self.line_is_in_docstring = True
            self._docstring_now = True
            self.docstring_data.append(line)
        elif self._docstring_now and self.DOCSTRING_SAME_END.match(line):
            self._handle_same_line_end(line)
        elif self._docstring_now and self.DOCSTRING_END.match(line):
            self._docstring_now = False
            self.line_is_in_docstring = True
            self.docstring_end = True
        elif self._docstring_now:
            self.line_is_in_docstring = True
            self.docstring_data.append(line)
        return line
