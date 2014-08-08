from collections import defaultdict
import inspect
from itertools import takewhile, chain
from os import path
import sys

from mako.template import Template

from tokenizer import *
from utils import isalambda, Colors
from quex_tokens import token_map


try:
    from IPython.core import ultratb

    sys.excepthook = ultratb.FormattedTB(
        mode='Verbose', color_scheme='Linux', call_pdb=1
    )
except ImportError, i:
    pass

LANGUAGE = "cpp"

languages_extensions = {
    "ada": "adb",
    "cpp": "cpp",
}

basic_types = {
    "ada": {
        long: "Long_Integer",
        bool: "Boolean"
    },
    "cpp": {
        long: "long",
        bool: "bool"
    }
}

null_constants = {
    "ada": "null",
    "cpp": "nullptr"
}


def c_repr(string):
    return '"{0}"'.format(repr(string)[1:-1].replace('"', r'\"'))


def get_type(typ):
    return basic_types[LANGUAGE][typ]


def null_constant():
    return null_constants[LANGUAGE]


def is_row(combinator):
    return isinstance(combinator, Row)


def is_discard(combinator):
    return isinstance(combinator, Discard)

###############
# AST HELPERS #
###############


def decl_type(ada_type):
    res = ada_type.as_string()
    return res.strip() + ("*" if ada_type.is_ptr else "")


class TemplateEnvironment(object):
    """
    Environment that gathers names for template processing.

    Names are associated to values with the attribute syntax.
    """

    def __init__(self, parent_env=None, **kwargs):
        """
        Create an environment and fill it with var (a dict) and with **kwargs.
        If `parent_env` is provided, the as_dict method will return a dict
        based on the parent's.
        """
        self.parent_env = parent_env
        self.update(kwargs)

    def update(self, vars):
        for name, value in vars.iteritems():
            setattr(self, name, value)

    def as_dict(self):
        """
        Return all names in this environment and the corresponding values as a
        dict.
        """
        result = self.parent_env.as_dict() if self.parent_env else {}
        result.update(self.__dict__)
        return result

    def __setattr__(self, name, value):
        if name == 'as_dict':
            raise TypeError('This attribute is reserved')
        else:
            super(TemplateEnvironment, self).__setattr__(name, value)


def render_template(template_name, template_env=None, **kwargs):
    """
    Render the Mako template `template_name` providing it a context that
    includes the names in `template_env` plus the ones in **kwargs. Return the
    resulting string.
    """
    context = {
        'c_repr':           c_repr,
        'get_type':         get_type,
        'null_constant':    null_constant,
        'is_row':           is_row,
        'is_discard':       is_discard,

        'is_class':         inspect.isclass,
        'decl_type':        decl_type,
    }
    if template_env:
        context.update(template_env.as_dict())
    context.update(kwargs)

    # "self" is a reserved name in Mako, so our variables cannot use it.
    # TODO: don't use "_self" at all in templates. Use more specific names
    # instead.
    if context.has_key('self'):
        context['_self'] = context.pop('self')

    return mako_template(template_name).render(**context)


template_cache = {}


def mako_template(file_name):
    t_path = path.join(path.dirname(path.realpath(__file__)),
                       LANGUAGE, "templates", file_name + ".mako")
    t = template_cache.get(t_path, None)

    if not t:
        t = Template(strict_undefined=True, filename=t_path)
        template_cache[t_path] = t

    return t


__next_ids = defaultdict(int)


def gen_name(var_name):
    __next_ids[var_name] += 1
    return "{0}_{1}".format(var_name, __next_ids[var_name])


def gen_names(*var_names):
    for var_name in var_names:
        yield gen_name(var_name)


###############
# Combinators #
###############

class AdaType(object):

    is_ptr = True

    def create_type_declaration(self):
        raise NotImplementedError()

    def add_to_context(self, compile_ctx, comb):
        raise NotImplementedError()

    def create_type_definition(self, compile_ctx, source_combinator):
        raise NotImplementedError()

    def create_instantiation(self, args):
        raise NotImplementedError()

    def name(self):
        raise NotImplementedError()

    def nullexpr(self):
        raise NotImplementedError()

    @classmethod
    def as_string(cls):
        return cls.__name__


class BoolType(AdaType):
    is_ptr = False

    @classmethod
    def as_string(cls):
        return get_type(bool)

    @classmethod
    def nullexpr(cls):
        return "false"


class LongType(AdaType):
    is_ptr = False

    @classmethod
    def as_string(cls):
        return get_type(long)

    @classmethod
    def nullexpr(cls):
        return None


class TokenType:
    pass


class Field(object):
    def __init__(self, name, type=None,
                 repr=False, kw_repr=False, opt=False, norepr_null=False):
        self.name = name
        self.kw_repr = kw_repr
        self.repr = repr
        self.type = type
        self.norepr_null = norepr_null
        self.opt = opt


class AstNodeMetaclass(type):
    def __init__(cls, name, base, dct):
        super(AstNodeMetaclass, cls).__init__(name, base, dct)
        cls.fields = dct.get("fields", [])
        cls.abstract = dct.get("abstract", False)


class ASTNode(AdaType):
    abstract = False
    fields = []
    __metaclass__ = AstNodeMetaclass

    def __init__(self, *args):
        for field, field_val in zip(self.fields, args):
            setattr(self, field.name, field_val)

    @classmethod
    def create_type_declaration(cls):
        return render_template('astnode_type_decl', cls=cls)

    @classmethod
    def create_type_definition(cls, compile_ctx, types):
        base_class = cls.__bases__[0]

        t_env = TemplateEnvironment(
            cls=cls, types=types, base_name=base_class.name()
        )
        tdef = render_template('astnode_type_def', t_env)
        if cls.is_ptr:
            compile_ctx.types_definitions.append(tdef)
        else:
            compile_ctx.val_types_definitions.append(tdef)

        t_env.repr_m_to_fields = [
            (m, f) for m, f in zip(types, cls.fields) if f.repr
        ]
        compile_ctx.body.append(render_template('astnode_type_impl', t_env))

    @classmethod
    def get_fields(cls):
        b = cls.__bases__[0]
        bfields = b.fields if b != ASTNode else []
        return bfields + cls.fields

    @classmethod
    def add_to_context(cls, compile_ctx, comb=None):
        if not cls in compile_ctx.types:
            if not comb:
                matchers = []
            elif isinstance(comb, Row):
                matchers = [m for m in comb.matchers if not isinstance(m, _)]
                matchers = matchers[-len(cls.fields):]
            else:
                matchers = [comb]

            types = [m.get_type() for m in matchers]

            base_class = cls.__bases__[0]
            if issubclass(base_class, ASTNode) and base_class != ASTNode:
                bcomb = comb if not cls.fields else None
                base_class.add_to_context(compile_ctx, bcomb)

            compile_ctx.types.add(cls)
            compile_ctx.types_declarations.append(
                cls.create_type_declaration())
            cls.create_type_definition(compile_ctx, types)

    @classmethod
    def name(cls):
        return cls.__name__

    @classmethod
    def repr_name(cls):
        return getattr(cls, "_repr_name", cls.name())

    @classmethod
    def nullexpr(cls):
        if cls.is_ptr:
            return null_constant()
        else:
            return "nil_{0}".format(cls.name())


def resolve(matcher):
    """
    :type matcher: Combinator|Token|ParserContainer
    :rtype: Combinator
    """
    if isinstance(matcher, Combinator):
        return matcher
    elif isinstance(matcher, type) and issubclass(matcher, Token):
        return TokClass(matcher)
    elif isinstance(matcher, Token):
        return Tok(matcher)
    elif isinstance(matcher, str):
        return Tok(Token(matcher))
    elif isalambda(matcher):
        return Defer(matcher)
    else:
        return Defer(matcher)


def indent(string, indent_level=3):
    return "\n".join((" " * indent_level) + s if s else ""
                     for s in string.splitlines())


class CompileCtx():
    def __init__(self):
        self.body = []
        self.types_declarations = []
        self.types_definitions = []
        self.val_types_definitions = []
        self.fns_decls = []
        self.fns = set()
        self.generic_vectors = set()
        self.types = set()
        self.main_comb = ""
        self.diag_types = []
        self.test_bodies = []
        self.test_names = []
        self.rules_to_fn_names = {}

    def get_header(self):
        return render_template(
            'main_header',
            self=self,
            tdecls=map(indent, self.types_declarations),
            tdefs=map(indent, self.types_definitions),
            fndecls=map(indent, self.fns_decls),
        )

    def get_source(self, header_name):
        return render_template(
            'main_body',
            self=self, header_name=header_name,
            bodies=map(indent, self.body)
        )

    def get_interactive_main(self, header_name):
        return render_template(
            'interactive_main',
            self=self, header_name=header_name
        )

    def has_type(self, typ):
        return typ in self.types


class Grammar(object):

    def __init__(self):
        self.resolved = False
        self.rules = {}

    def add_rules(self, **kwargs):
        for name, rule in kwargs.items():
            self.rules[name] = rule
            rule.set_name(name)
            rule.set_grammar(self)
            rule.is_root = True

    def __getattr__(self, item_name):
        if item_name in self.rules:
            r = self.rules[item_name]
            return Defer(lambda: r)

        if not self.resolved:
            return Defer(lambda: self.rules[item_name])
        else:
            raise AttributeError

    def dump_to_file(self, file_path=".", file_name="parse"):
        ctx = CompileCtx()
        for r_name, r in self.rules.items():

            r.compile(ctx)
            ctx.rules_to_fn_names[r_name] = r

        with open(path.join(file_path, file_name + ".cpp"), "w") as f:
            f.write(ctx.get_source(header_name=file_name + ".hpp"))

        with open(path.join(file_path, file_name + ".hpp"), "w") as f:
            f.write(ctx.get_header())

        with open(path.join(file_path, file_name + "_main.cpp"), "w") as f:
            f.write(ctx.get_interactive_main(header_name=file_name + ".hpp"))


class Combinator(object):

    # noinspection PyMissingConstructor
    def __init__(self):
        self._mod = None
        self.gen_fn_name = gen_name(self.__class__.__name__ + "_parse")
        self.grammar = None
        self.is_root = False
        self._name = ""
        self.res = None
        self.pos = None

    def needs_refcount(self):
        return True

    def __or__(self, other):
        other_comb = resolve(other)
        if isinstance(other_comb, Or):
            other_comb.matchers.append(self)
            return other_comb
        elif isinstance(self, Or):
            self.matchers.append(other_comb)
            return self
        else:
            return Or(self, other_comb)

    def __xor__(self, transform_fn):
        """
        :type transform_fn: (T) => U
        :rtype: Transform
        """
        return Transform(self, transform_fn)

    def set_grammar(self, grammar):
        for c in self.children():
            c.set_grammar(grammar)
        self.grammar = grammar

    def set_name(self, name):
        for c in self.children():
            if c._name and not isinstance(c, Defer):
                c.set_name(name)
            self._name = name
        self.gen_fn_name = gen_name("{0}_{1}_parse".format(
            name, self.__class__.__name__.lower()))

    def parse(self, tkz, pos):
        raise NotImplemented

    # noinspection PyMethodMayBeStatic
    def children(self):
        return []

    def compile(self, compile_ctx=None):
        """:type compile_ctx: CompileCtx"""
        t_env = TemplateEnvironment()
        t_env.self = self

        # Verify that the function hasn't been compiled yet
        if self.gen_fn_name in compile_ctx.fns:
            return
        compile_ctx.fns.add(self.gen_fn_name)

        if not compile_ctx:
            compile_ctx = CompileCtx()

        t_env.pos, t_env.res, t_env.code, t_env.defs = (
            self.generate_code(compile_ctx=compile_ctx)
        )
        t_env.code = indent(t_env.code)
        t_env.fn_profile = render_template('combinator_fn_profile', t_env)
        t_env.fn_code = render_template('combinator_fn_code', t_env)

        compile_ctx.body.append(t_env.fn_code)
        compile_ctx.fns_decls.append(t_env.fn_profile)

    def compile_and_exec(self, compile_ctx=None):
        raise NotImplementedError()

    def force_fn_call(self):
        return self.is_root

    def get_type(self):
        raise NotImplementedError()

    def gen_code_or_fncall(self, compile_ctx, pos_name="pos",
                           force_fncall=False):

        if self._name:
            print "Compiling rule : {0}".format(
                Colors.HEADER + self.gen_fn_name + Colors.ENDC
            )

        if self.force_fn_call() or force_fncall or \
                self.gen_fn_name in compile_ctx.fns:

            self.compile(compile_ctx)
            self.pos, self.res = gen_names("fncall_pos", "fncall_res")

            fncall_block = render_template(
                'combinator_fncall',
                self=self, pos_name=pos_name
            )
            return self.pos, self.res, fncall_block, [
                (self.pos, LongType),
                (self.res, self.get_type())
            ]
        else:
            pos, res, code, decls = self.generate_code(compile_ctx, pos_name)
            self.res = res
            self.pos = pos
            return pos, res, code, decls

    def generate_code(self, compile_ctx, pos_name="pos"):
        raise NotImplemented

    def test_parser(self, ada_string):
        tkz = make_ada_tokenizer(ada_string)
        npos, res = self.parse(tkz, 0)
        return res


class Tok(Combinator):

    def __repr__(self):
        return "Tok({0})".format(repr(self.tok.val))

    def needs_refcount(self):
        return False

    def __init__(self, tok):
        """ :type tok: Token """
        Combinator.__init__(self)
        self.tok = tok
        self._id = token_map.names_to_ids[token_map.str_to_names[tok.val]]

    def get_type(self):
        return Token

    def generate_code(self, compile_ctx, pos_name="pos"):
        pos, res = gen_names("tk_pos", "tk_res")
        repr_tok_val = repr(self.tok.val)
        code = render_template(
            'tok_code',
            self=self, pos_name=pos_name,
            pos=pos, res=res,
        )
        return pos, res, code, [(pos, LongType), (res, Token)]


class TokClass(Combinator):

    classes_to_identifiers = {
        Id: token_map.names_to_ids['IDENTIFIER'],
        Lbl: token_map.names_to_ids['LABEL'],
        NumLit: token_map.names_to_ids['NUMBER'],
        CharLit: token_map.names_to_ids['CHAR'],
        StringLit: token_map.names_to_ids['STRING'],
        NoToken: token_map.names_to_ids['TERMINATION'],
    }

    def needs_refcount(self):
        return False

    def __repr__(self):
        return "TokClass({0})".format(self.tok_class.__name__)

    def __init__(self, tok_class):
        Combinator.__init__(self)
        self.tok_class = tok_class

    def get_type(self):
        return Token

    def generate_code(self, compile_ctx, pos_name="pos"):
        pos, res = gen_names("tk_class_pos", "tk_class_res")
        _id = self.classes_to_identifiers[self.tok_class]
        code = render_template(
            'tokclass_code',
            self=self, pos_name=pos_name,
            pos=pos, res=res, _id=_id,
        )
        return pos, res, code, [(pos, LongType), (res, Token)]


def common_ancestor(*cs):
    assert all(inspect.isclass(c) for c in cs)
    rmro = lambda k: reversed(k.mro())
    return list(takewhile(lambda a: len(set(a)) == 1, zip(*map(rmro, cs))))[-1][0]


def get_combs_at_index(comb, idx):
    if isinstance(comb, Or):
        return list(chain(*(get_combs_at_index(subc, idx)
                            for subc in comb.matchers)))
    elif isinstance(comb, Row):
        return get_combs_at_index(comb.matchers[idx], 0) if len(comb.matchers) > idx else []
    elif isinstance(comb, Opt):
        return get_combs_at_index(comb.matcher, idx)
    elif isinstance(comb, Transform):
        return get_combs_at_index(comb.combinator, idx)
    else:
        if idx == 0:
            return [comb]
        else:
            return None


def group_by(lst, transform_fn=None):
    if not transform_fn:
        transform_fn = lambda x: x

    res = defaultdict(list)

    for el in lst:
        t = transform_fn(el)
        res[transform_fn(el)].append(el)

    if res.get(None):
        del res[None]

    return res.values()


def get_comb_groups(comb, idx):
    def tr(c):
        if isinstance(c, Defer):
            c.resolve_combinator()
            return c.combinator
        return None
    return group_by(get_combs_at_index(comb, idx), tr)


class Or(Combinator):

    def __repr__(self):
        return "Or({0})".format(", ".join(repr(m) for m in self.matchers))

    def __init__(self, *matchers):
        """ :type matchers: list[Combinator|Token|type] """
        Combinator.__init__(self)
        self.matchers = [resolve(m) for m in matchers]
        self.locked = False
        self.cached_type = None

    def children(self):
        return self.matchers

    def needs_refcount(self):
        assert(all(i.needs_refcount() == self.matchers[0].needs_refcount()
                   for i in self.matchers))
        return self.matchers[0].needs_refcount()

    def get_type(self):
        if self.cached_type:
            return self.cached_type
        if self.locked:
            return None
        try:
            self.locked = True
            types = set()
            for m in self.matchers:
                t = m.get_type()
                if t:
                    types.add(t)

            if all(inspect.isclass(t) for t in types):
                res = common_ancestor(*types)
            else:
                typs = list(types)
                assert all(type(t) == type(typs[0]) for t in typs)
                res = typs[0]

            self.cached_type = res
            return res
        finally:
            self.locked = False

    def generate_code(self, compile_ctx, pos_name="pos"):
        pos, res = gen_names("or_pos", "or_res")

        t_env = TemplateEnvironment()
        t_env.self = self

        t_env.results = [
            m.gen_code_or_fncall(compile_ctx, pos_name)
            for m in self.matchers
        ]
        t_env.decls = list(chain(
            [(pos, LongType), (res, self.get_type())],
            *[r[3] for r in t_env.results]
        ))
        t_env.exit_label = gen_name("Exit_Or")
        code = render_template(
            'or_code', t_env,
            pos=pos, res=res,
            typ=decl_type(self.get_type()),
        )
        return pos, res, code, t_env.decls


class RowType(AdaType):

    is_ptr = True

    def __init__(self, name):
        self.name = name

    def as_string(self):
        return self.name

    def nullexpr(self):
        return null_constant()


class Row(Combinator):

    def needs_refcount(self):
        return True

    def __repr__(self):
        return "Row({0})".format(", ".join(repr(m) for m in self.matchers))

    def __init__(self, *matchers):
        """ :type matchers: list[Combinator|Token|type] """
        Combinator.__init__(self)
        self.matchers = [resolve(m) for m in matchers]
        self.make_tuple = True
        self.typ = RowType(gen_name("Row"))
        self.components_need_inc_ref = True

    def children(self):
        return self.matchers

    def get_type(self):
        return self.typ

    def create_type(self, compile_ctx):
        t_env = TemplateEnvironment(
            matchers=[m for m in self.matchers if not isinstance(m, _)]
        )
        t_env.self = self

        compile_ctx.types_declarations.append(
            render_template('row_type_decl', t_env)
        )

        compile_ctx.types_definitions.insert(0, render_template(
            'row_type_def', t_env
        ))

        compile_ctx.body.append(render_template(
            'row_type_impl', t_env
        ))

    def generate_code(self, compile_ctx, pos_name="pos"):
        """ :type compile_ctx: CompileCtx """
        t_env = TemplateEnvironment(pos_name=pos_name)
        t_env.self=self

        c_pos_name = pos_name
        t_env.pos, t_env.res, t_env.did_fail = gen_names(
            "row_pos", "row_res", "row_did_fail"
        )

        # Create decls for declarative part of the parse subprogram.
        # If make_tuple is false we don't want a variable for the result.
        decls = [(t_env.pos, LongType), (t_env.did_fail, BoolType)]

        if self.make_tuple:
            decls.append((t_env.res, self.get_type()))
            self.create_type(compile_ctx)

        t_env.subresults = list(gen_names(*[
            "row_subres_{0}".format(i)
            for i in range(len(self.matchers))
        ]))
        t_env.exit_label = gen_name("row_exit_label")

        tokeep_matchers = [m for m in self.matchers if not isinstance(m, _)]
        self.args = [r for r, m in zip(t_env.subresults, self.matchers)
                     if not isinstance(m, _)]

        bodies = []
        for i, (matcher, subresult) in enumerate(zip(self.matchers,
                                                     t_env.subresults)):
            t_subenv = TemplateEnvironment(
                t_env,
                matcher=matcher, subresult=subresult, i=i,
            )
            (t_subenv.mpos,
             t_subenv.mres,
             t_subenv.m_code,
             t_subenv.m_decls) = (
                matcher.gen_code_or_fncall(compile_ctx, t_env.pos)
            )
            decls += t_subenv.m_decls
            if not is_discard(matcher):
                decls.append((subresult, matcher.get_type()))

            bodies.append(render_template('row_submatch', t_subenv))

        code = render_template('row_code', t_env, body='\n'.join(bodies))
        return t_env.pos, t_env.res, code, decls

    def __rshift__(self, index):
        return Extract(self, index)


class ListType(AdaType):

    is_ptr = True

    def __init__(self, el_type):
        self.el_type = el_type

    def as_string(self):
        return render_template('list_type', el_type=self.el_type)

    def nullexpr(self):
        return null_constant()


class List(Combinator):

    def needs_refcount(self):
        return self.parser.needs_refcount()

    def __repr__(self):
        return "List({0})".format(
            repr(self.parser) + (", sep={0}".format(self.sep)
                                 if self.sep else "")
        )

    def __init__(self, parser, sep=None, empty_valid=False, revtree=None):
        """
        :type sep: Token|string
        :type empty_valid: bool
        """
        Combinator.__init__(self)
        self.parser = resolve(parser)
        self.sep = resolve(sep) if sep else None
        self.empty_valid = empty_valid
        self.revtree_class = revtree

        if empty_valid:
            assert not self.revtree_class

    def children(self):
        return [self.parser]

    def get_type(self):
        if self.revtree_class:
            return common_ancestor(self.parser.get_type(), self.revtree_class)
        else:
            return ListType(self.parser.get_type())

    def generate_code(self, compile_ctx, pos_name="pos"):
        """:type compile_ctx: CompileCtx"""
        t_env = TemplateEnvironment(
            pos_name=pos_name
        )
        t_env.self = self

        t_env.pos, t_env.res, t_env.cpos = gen_names(
            "lst_pos", "lst_res", "lst_cpos"
        )
        seps = gen_name("lst_seps")
        t_env.ppos, t_env.pres, t_env.pcode, t_env.pdecls = (
            self.parser.gen_code_or_fncall(compile_ctx, t_env.cpos)
        )
        t_env.pcode = indent(t_env.pcode)
        compile_ctx.generic_vectors.add(self.parser.get_type().as_string())
        decls = [(t_env.pos, LongType),
                 (t_env.res, self.get_type()),
                 (t_env.cpos, LongType)] + t_env.pdecls

        if self.revtree_class:
            if not self.revtree_class in compile_ctx.types:
                compile_ctx.types.add(self.revtree_class)
                self.revtree_class.create_type_definition(
                    compile_ctx, [self.get_type(), self.get_type()]
                )

        (t_env.sep_pos,
         t_env.sep_res,
         t_env.sep_code,
         t_env.sep_decls) = (
            self.sep.gen_code_or_fncall(compile_ctx, t_env.cpos)
            if self.sep else
            (None, None, None, None)
        )
        if t_env.sep_decls:
            decls += t_env.sep_decls

        code = render_template('list_code', t_env)
        return t_env.pos, t_env.res, code, decls


class Opt(Combinator):

    def needs_refcount(self):
        if self._booleanize:
            return False
        return self.matcher.needs_refcount()

    def __repr__(self):
        return "Opt({0})".format(self.matcher)

    def __init__(self, matcher, *matchers):
        Combinator.__init__(self)
        self._booleanize = False
        if matchers:
            self.matcher = Row(matcher, *matchers)
        else:
            self.matcher = resolve(matcher)

    def as_bool(self):
        self._booleanize = True
        return self

    def children(self):
        return [self.matcher]

    def get_type(self):
        return BoolType if self._booleanize else self.matcher.get_type()

    def generate_code(self, compile_ctx, pos_name="pos"):
        t_env = TemplateEnvironment(pos_name=pos_name)
        t_env.self = self

        t_env.mpos, t_env.mres, t_env.code, decls = (
            self.matcher.gen_code_or_fncall(compile_ctx, pos_name)
        )
        t_env.bool_res = gen_name("opt_bool_res")

        code = render_template('opt_code', t_env)

        if self._booleanize:
            decls.append((t_env.bool_res, BoolType))
            t_env.mres = t_env.bool_res

        return t_env.mpos, t_env.mres, code, decls

    def __rshift__(self, index):
        m = self.matcher
        assert isinstance(m, Row)
        return Opt(Extract(m, index))


class Extract(Combinator):

    def needs_refcount(self):
        return self.comb.needs_refcount()

    def __repr__(self):
        return "{0} >> {1}".format(self.comb, self.index)

    def __init__(self, comb, index):
        """
        :param Row comb: The combinator that will serve as target for
        extract operation
        :param int index: The index you want to extract from the row
        """
        Combinator.__init__(self)
        self.comb = comb
        self.index = index
        assert isinstance(self.comb, Row)
        self.comb.components_need_inc_ref = False

    def children(self):
        return [self.comb]

    def get_type(self):
        return self.comb.matchers[self.index].get_type()

    def generate_code(self, compile_ctx, pos_name="pos"):
        self.comb.make_tuple = False
        cpos, cres, code, decls = self.comb.gen_code_or_fncall(
            compile_ctx, pos_name)
        args = self.comb.args
        return cpos, args[self.index], code, decls


class Discard(Combinator):

    def needs_refcount(self):
        return self.parser.needs_refcount()

    def __repr__(self):
        return "Discard({0})".format(self.parser)

    def __init__(self, parser):
        Combinator.__init__(self)
        self.parser = resolve(parser)

    def children(self):
        return [self.parser]

    def get_type(self):
        return self.parser.get_type()

    def generate_code(self, compile_ctx, pos_name="pos"):
        return self.parser.gen_code_or_fncall(compile_ctx, pos_name)


_ = Discard


class Defer(Combinator):

    def needs_refcount(self):
        self.resolve_combinator()
        return self.combinator.needs_refcount()

    def __repr__(self):
        self.resolve_combinator()
        return "Defer({0})".format(getattr(self.combinator, "_name", ".."))

    def __init__(self, parser_fn):
        Combinator.__init__(self)
        self.parser_fn = parser_fn
        self.combinator = None
        ":type: Combinator"

    def resolve_combinator(self):
        if not self.combinator:
            self.combinator = self.parser_fn()

    def get_type(self):
        self.resolve_combinator()
        return self.combinator.get_type()

    def generate_code(self, compile_ctx, pos_name="pos"):
        self.resolve_combinator()
        return self.combinator.gen_code_or_fncall(
            compile_ctx, pos_name=pos_name, force_fncall=True
        )


class Transform(Combinator):

    def needs_refcount(self):
        return True

    def __repr__(self):
        return "{0} ^ {1}".format(self.combinator, self.typ.name())

    def __init__(self, combinator, typ):
        Combinator.__init__(self)
        assert issubclass(typ, AdaType)
        self.combinator = combinator
        self.typ = typ
        ":type: AdaType"

        self._is_ptr = typ.is_ptr

    def children(self):
        return [self.combinator]

    def get_type(self):
        return self.typ

    def generate_code(self, compile_ctx, pos_name="pos"):
        """:type compile_ctx: CompileCtx"""
        t_env = TemplateEnvironment()
        t_env.self = self

        self.typ.add_to_context(compile_ctx, self.combinator)

        if isinstance(self.combinator, Row):
            self.combinator.make_tuple = False

        t_env.cpos, t_env.cres, t_env.code, decls = (
            self.combinator.gen_code_or_fncall(compile_ctx, pos_name)
        )
        t_env.args = (
            self.combinator.args
            if isinstance(self.combinator, Row) else
            [t_env.cres]
        )

        t_env.res = gen_name("transform_res")
        code = render_template('transform_code', t_env)
        compile_ctx.diag_types.append(self.typ)

        return t_env.cpos, t_env.res, code, decls + [
            (t_env.res, self.get_type())
        ]


class Success(Combinator):

    def __repr__(self):
        return "Success({0})".format(self.typ.name())

    def needs_refcount(self):
        return True

    def __init__(self, result_typ):
        Combinator.__init__(self)
        self.typ = result_typ

    def children(self):
        return []

    def get_type(self):
        return self.typ

    def generate_code(self, compile_ctx, pos_name="pos"):
        self.typ.add_to_context(compile_ctx, None)
        res = gen_name("success_res")
        code = render_template('success_code', self=self, res=res)

        return pos_name, res, code, [(res, self.get_type())]


class Null(Success):

    def __repr__(self):
        return "Null"

    def generate_code(self, compile_ctx, pos_name="pos"):
        typ = self.get_type()
        if isinstance(typ, ASTNode):
            self.get_type().add_to_context(compile_ctx, None)
        res = gen_name("null_res")
        code = render_template('null_code', self=self, res=res)
        return pos_name, res, code, [(res, self.get_type())]

    def get_type(self):
        return (self.typ if inspect.isclass(self.typ)
                and issubclass(self.typ, AdaType)
                else self.typ.get_type())


class EnumType(AdaType):
    is_ptr = False
    alternatives = []

    def __init__(self, alt):
        assert alt in self.alternatives
        self.alt = alt

    @classmethod
    def name(cls):
        return cls.__name__

    @classmethod
    def add_to_context(cls, compile_ctx, comb=None):
        if not cls in compile_ctx.types:
            compile_ctx.types.add(cls)
            compile_ctx.types_declarations.append(
                render_template('enum_type_decl', cls=cls)
            )
            compile_ctx.body.append(
                render_template('enum_type_impl', cls=cls)
            )

    @classmethod
    def nullexpr(cls):
        return cls.name() + "::uninitialized"


class Enum(Combinator):

    def needs_refcount(self):
        return False

    def __repr__(self):
        return "Enum({0}, {1})".format(self.combinator, self.enum_type_inst)

    def __init__(self, combinator, enum_type_inst):
        Combinator.__init__(self)
        self.combinator = resolve(combinator) if combinator else None
        self.enum_type_inst = enum_type_inst

    def children(self):
        return []

    def get_type(self):
        return self.enum_type_inst.__class__

    def generate_code(self, compile_ctx, pos_name="pos"):
        self.enum_type_inst.add_to_context(compile_ctx)

        res = gen_name("enum_res")

        if self.combinator:
            if isinstance(self.combinator, Row):
                self.combinator.make_tuple = False

            cpos, _, code, decls = self.combinator.gen_code_or_fncall(
                compile_ctx, pos_name
            )
        else:
            cpos, code, decls = pos_name, "", []

        body = render_template(
            'enum_code',
            self=self, res=res, cpos=cpos, code=code)

        return cpos, res, body, [(res, self.get_type())] + decls
