#!/usr/bin/env python

from __future__ import absolute_import, division, print_function

import os
import sys

import libadalang as lal
from libadalang import _py2to3


desc = """
Go over Ada files given as argument, processing any lines starting with --% and
containing a python expression starting with $. For those lines, eval the
expression in a context containing the following variables:

- `node` is the ast node immediately preceding the comment.
- `lal` is the libadalang module.
"""


YELLOW = '\033[33m'


def col(msg, color):
    return msg if not sys.stdout.isatty() else color + msg + '\033[0m'


class InlinePlayground(lal.App):

    def main(self):
        for unit in self.units.values():
            self.process_unit(unit)

    def description(self):
        return desc

    def add_arguments(self):
        self.parser.add_argument(
            '--pretty-out', action='store_true',
            default=os.isatty(sys.stdout.fileno()),
            help='Prettify output for CLI use'
        )
        super(InlinePlayground, self).add_arguments()

    def process_unit(self, unit):

        def previous_not_trivia(token):
            ret = token
            while ret.is_trivia:
                ret = ret.previous
            return ret

        for tok in unit.iter_tokens():
            if tok.kind == 'Comment' and tok.text.startswith('--%'):
                expr_text = tok.text[3:].strip()
                if expr_text.startswith('$'):
                    expr_text = expr_text[1:].strip()
                current_node = unit.root.lookup(
                    previous_not_trivia(tok).sloc_range.start
                )
                print("Eval '{}' on node {}".format(
                    col(expr_text, YELLOW),
                    col(current_node.entity_repr, YELLOW)
                ))
                try:
                    value = eval(
                        expr_text, None,
                        {'lal': lal, 'node': current_node}
                    )
                except lal.PropertyError as pe:
                    print('Exception:', *pe.args)
                else:

                    # Hide discrepancies between Python2 and Python3
                    value_repr = (_py2to3.text_repr(value)
                                  if isinstance(value, _py2to3.text_type) else
                                  repr(value))
                    print('Result: {}'.format(col(value_repr, YELLOW)))
                print()

        print()

if __name__ == '__main__':
    InlinePlayground.run()
