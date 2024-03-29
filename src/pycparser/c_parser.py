#------------------------------------------------------------------------------
# pycparser: c_parser.py
#
# CParser class: Parser and AST builder for the C language
#
# Eli Bendersky [http://eli.thegreenplace.net]
# License: BSD
#------------------------------------------------------------------------------
import re
import pydot

from ply import yacc

import c_ast
from c_lexer import CLexer
from plyparser import PLYParser, Coord, ParseError
from ast_transforms import fix_switch_cases

# graph = pydot.Dot(graph_type='digraph')

counter = 0

class CParser(PLYParser):
    def __init__(
            self,
            lex_optimize=True,
            lexer=CLexer,
            lextab='pycparser.lextab',
            yacc_optimize=False,
            yacctab='pycparser.yacctab',
            yacc_debug=False,
            taboutputdir='',
            graph=None):
        """ Create a new CParser.

            Some arguments for controlling the debug/optimization
            level of the parser are provided. The defaults are
            tuned for release/performance mode.
            The simple rules for using them are:
            *) When tweaking CParser/CLexer, set these to False
            *) When releasing a stable parser, set to True

            lex_optimize:
                Set to False when you're modifying the lexer.
                Otherwise, changes in the lexer won't be used, if
                some lextab.py file exists.
                When releasing with a stable lexer, set to True
                to save the re-generation of the lexer table on
                each run.

            lexer:
                Set this parameter to define the lexer to use if
                you're not using the default CLexer.

            lextab:
                Points to the lex table that's used for optimized
                mode. Only if you're modifying the lexer and want
                some tests to avoid re-generating the table, make
                this point to a local lex table file (that's been
                earlier generated with lex_optimize=True)

            yacc_optimize:
                Set to False when you're modifying the parser.
                Otherwise, changes in the parser won't be used, if
                some parsetab.py file exists.
                When releasing with a stable parser, set to True
                to save the re-generation of the parser table on
                each run.

            yacctab:
                Points to the yacc table that's used for optimized
                mode. Only if you're modifying the parser, make
                this point to a local yacc table file

            yacc_debug:
                Generate a parser.out file that explains how yacc
                built the parsing table from the grammar.

            taboutputdir:
                Set this parameter to control the location of generated
                lextab and yacctab files.
        """

        global counter
        self.graph = graph
        self.clex = lexer(
            error_func=self._lex_error_func,
            on_lbrace_func=self._lex_on_lbrace_func,
            on_rbrace_func=self._lex_on_rbrace_func,
            type_lookup_func=self._lex_type_lookup_func)

        self.clex.build(
            optimize=lex_optimize,
            lextab=lextab,
            outputdir=taboutputdir)
        self.tokens = self.clex.tokens

        rules_with_opt = [
            'abstract_declarator',
            'assignment_expression',
            'declaration_list',
            'declaration_specifiers',
            'designation',
            'expression',
            'identifier_list',
            'init_declarator_list',
            'initializer_list',
            'parameter_type_list',
            'specifier_qualifier_list',
            'block_item_list',
            'type_qualifier_list',
            'struct_declarator_list'
        ]
        global counter
        for rule in rules_with_opt:
            counter = self._create_opt_rule(rule, counter)
            print "xxxxxxxxxxxxxxxxxx ", rule

        self.cparser = yacc.yacc(
            module=self,
            start='translation_unit_or_empty',
            debug=yacc_debug,
            optimize=yacc_optimize,
            tabmodule=yacctab,
            outputdir=taboutputdir)

        # Stack of scopes for keeping track of symbols. _scope_stack[-1] is
        # the current (topmost) scope. Each scope is a dictionary that
        # specifies whether a name is a type. If _scope_stack[n][name] is
        # True, 'name' is currently a type in the scope. If it's False,
        # 'name' is used in the scope but not as a type (for instance, if we
        # saw: int name;
        # If 'name' is not a key in _scope_stack[n] then 'name' was not defined
        # in this scope at all.
        self._scope_stack = [dict()]

        # Keeps track of the last token given to yacc (the lookahead token)
        self._last_yielded_token = None

    def parse(self, text, filename='', debuglevel=0):
        """ Parses C code and returns an AST.

            text:
                A string containing the C source code

            filename:
                Name of the file being parsed (for meaningful
                error messages)

            debuglevel:
                Debug level to yacc
        """
        self.clex.filename = filename
        self.clex.reset_lineno()
        self._scope_stack = [dict()]
        self._last_yielded_token = None
        return self.cparser.parse(
                input=text,
                lexer=self.clex,
                debug=debuglevel), self.graph

    ######################--   PRIVATE   --######################

    def _push_scope(self):
        self._scope_stack.append(dict())

    def _pop_scope(self):
        assert len(self._scope_stack) > 1
        self._scope_stack.pop()

    def _add_typedef_name(self, name, coord):
        """ Add a new typedef name (ie a TYPEID) to the current scope
        """
        if not self._scope_stack[-1].get(name, True):
            self._parse_error(
                "Typedef %r previously declared as non-typedef "
                "in this scope" % name, coord)
        self._scope_stack[-1][name] = True

    def _add_identifier(self, name, coord):
        """ Add a new object, function, or enum member name (ie an ID) to the
            current scope
        """
        if self._scope_stack[-1].get(name, False):
            self._parse_error(
                "Non-typedef %r previously declared as typedef "
                "in this scope" % name, coord)
        self._scope_stack[-1][name] = False

    def _is_type_in_scope(self, name):
        """ Is *name* a typedef-name in the current scope?
        """
        for scope in reversed(self._scope_stack):
            # If name is an identifier in this scope it shadows typedefs in
            # higher scopes.
            in_scope = scope.get(name)
            if in_scope is not None: return in_scope
        return False

    def _lex_error_func(self, msg, line, column):
        self._parse_error(msg, self._coord(line, column))

    def _lex_on_lbrace_func(self):
        self._push_scope()

    def _lex_on_rbrace_func(self):
        self._pop_scope()

    def _lex_type_lookup_func(self, name):
        """ Looks up types that were previously defined with
            typedef.
            Passed to the lexer for recognizing identifiers that
            are types.
        """
        is_type = self._is_type_in_scope(name)
        return is_type

    def _get_yacc_lookahead_token(self):
        """ We need access to yacc's lookahead token in certain cases.
            This is the last token yacc requested from the lexer, so we
            ask the lexer.
        """
        return self.clex.last_token

    # To understand what's going on here, read sections A.8.5 and
    # A.8.6 of K&R2 very carefully.
    #
    # A C type consists of a basic type declaration, with a list
    # of modifiers. For example:
    #
    # int *c[5];
    #
    # The basic declaration here is 'int c', and the pointer and
    # the array are the modifiers.
    #
    # Basic declarations are represented by TypeDecl (from module c_ast) and the
    # modifiers are FuncDecl, PtrDecl and ArrayDecl.
    #
    # The standard states that whenever a new modifier is parsed, it should be
    # added to the end of the list of modifiers. For example:
    #
    # K&R2 A.8.6.2: Array Declarators
    #
    # In a declaration T D where D has the form
    #   D1 [constant-expression-opt]
    # and the type of the identifier in the declaration T D1 is
    # "type-modifier T", the type of the
    # identifier of D is "type-modifier array of T"
    #
    # This is what this method does. The declarator it receives
    # can be a list of declarators ending with TypeDecl. It
    # tacks the modifier to the end of this list, just before
    # the TypeDecl.
    #
    # Additionally, the modifier may be a list itself. This is
    # useful for pointers, that can come as a chain from the rule
    # p_pointer. In this case, the whole modifier list is spliced
    # into the new location.
    def _type_modify_decl(self, decl, modifier):
        """ Tacks a type modifier on a declarator, and returns
            the modified declarator.

            Note: the declarator and modifier may be modified
        """
        #~ print '****'
        #~ decl.show(offset=3)
        #~ modifier.show(offset=3)
        #~ print '****'

        modifier_head = modifier
        modifier_tail = modifier

        # The modifier may be a nested list. Reach its tail.
        #
        while modifier_tail.type:
            modifier_tail = modifier_tail.type

        # If the decl is a basic type, just tack the modifier onto
        # it
        #
        if isinstance(decl, c_ast.TypeDecl):
            modifier_tail.type = decl
            return modifier
        else:
            # Otherwise, the decl is a list of modifiers. Reach
            # its tail and splice the modifier onto the tail,
            # pointing to the underlying basic type.
            #
            decl_tail = decl

            while not isinstance(decl_tail.type, c_ast.TypeDecl):
                decl_tail = decl_tail.type

            modifier_tail.type = decl_tail.type
            decl_tail.type = modifier_head
            return decl

    # Due to the order in which declarators are constructed,
    # they have to be fixed in order to look like a normal AST.
    #
    # When a declaration arrives from syntax construction, it has
    # these problems:
    # * The innermost TypeDecl has no type (because the basic
    #   type is only known at the uppermost declaration level)
    # * The declaration has no variable name, since that is saved
    #   in the innermost TypeDecl
    # * The typename of the declaration is a list of type
    #   specifiers, and not a node. Here, basic identifier types
    #   should be separated from more complex types like enums
    #   and structs.
    #
    # This method fixes these problems.
    #
    def _fix_decl_name_type(self, decl, typename):
        """ Fixes a declaration. Modifies decl.
        """
        # Reach the underlying basic type
        #
        type = decl
        while not isinstance(type, c_ast.TypeDecl):
            type = type.type

        decl.name = type.declname
        type.quals = decl.quals

        # The typename is a list of types. If any type in this
        # list isn't an IdentifierType, it must be the only
        # type in the list (it's illegal to declare "int enum ..")
        # If all the types are basic, they're collected in the
        # IdentifierType holder.
        #
        for tn in typename:
            if not isinstance(tn, c_ast.IdentifierType):
                if len(typename) > 1:
                    self._parse_error(
                        "Invalid multiple types specified", tn.coord)
                else:
                    type.type = tn
                    return decl

        if not typename:
            # Functions default to returning int
            #
            if not isinstance(decl.type, c_ast.FuncDecl):
                self._parse_error(
                        "Missing type in declaration", decl.coord)
            type.type = c_ast.IdentifierType(
                    ['int'],
                    coord=decl.coord)
        else:
            # At this point, we know that typename is a list of IdentifierType
            # nodes. Concatenate all the names into a single list.
            #
            type.type = c_ast.IdentifierType(
                [name for id in typename for name in id.names],
                coord=typename[0].coord)
        return decl

    def _add_declaration_specifier(self, declspec, newspec, kind):
        """ Declaration specifiers are represented by a dictionary
            with the entries:
            * qual: a list of type qualifiers
            * storage: a list of storage type qualifiers
            * type: a list of type specifiers
            * function: a list of function specifiers

            This method is given a declaration specifier, and a
            new specifier of a given kind.
            Returns the declaration specifier, with the new
            specifier incorporated.
        """
        spec = declspec or dict(qual=[], storage=[], type=[], function=[])
        spec[kind].insert(0, newspec)
        return spec

    def _build_declarations(self, spec, decls, typedef_namespace=False):
        """ Builds a list of declarations all sharing the given specifiers.
            If typedef_namespace is true, each declared name is added
            to the "typedef namespace", which also includes objects,
            functions, and enum constants.
        """
        is_typedef = 'typedef' in spec['storage']
        declarations = []

        # Bit-fields are allowed to be unnamed.
        #
        if decls[0].get('bitsize') is not None:
            pass

        # When redeclaring typedef names as identifiers in inner scopes, a
        # problem can occur where the identifier gets grouped into
        # spec['type'], leaving decl as None.  This can only occur for the
        # first declarator.
        #
        elif decls[0]['decl'] is None:
            if len(spec['type']) < 2 or len(spec['type'][-1].names) != 1 or \
                    not self._is_type_in_scope(spec['type'][-1].names[0]):
                coord = '?'
                for t in spec['type']:
                    if hasattr(t, 'coord'):
                        coord = t.coord
                        break
                self._parse_error('Invalid declaration', coord)

            # Make this look as if it came from "direct_declarator:ID"
            decls[0]['decl'] = c_ast.TypeDecl(
                declname=spec['type'][-1].names[0],
                type=None,
                quals=None,
                coord=spec['type'][-1].coord)



            # Remove the "new" type's name from the end of spec['type']
            del spec['type'][-1]

        # A similar problem can occur where the declaration ends up looking
        # like an abstract declarator.  Give it a name if this is the case.
        #
        elif not isinstance(decls[0]['decl'],
                (c_ast.Struct, c_ast.Union, c_ast.IdentifierType)):
            decls_0_tail = decls[0]['decl']
            while not isinstance(decls_0_tail, c_ast.TypeDecl):
                decls_0_tail = decls_0_tail.type
            if decls_0_tail.declname is None:
                decls_0_tail.declname = spec['type'][-1].names[0]
                del spec['type'][-1]

        print "decls is: ", decls        
        for decl in decls:
            if "node_" in decl:
                continue
            print "decl is: ", decl
            assert decl['decl'] is not None
            if is_typedef:
                declaration = c_ast.Typedef(
                    name=None,
                    quals=spec['qual'],
                    storage=spec['storage'],
                    type=decl['decl'],
                    coord=decl['decl'].coord)
            else:
                declaration = c_ast.Decl(
                    name=None,
                    quals=spec['qual'],
                    storage=spec['storage'],
                    funcspec=spec['function'],
                    type=decl['decl'],
                    init=decl.get('init'),
                    bitsize=decl.get('bitsize'),
                    coord=decl['decl'].coord)

            print "declaration is: ", declaration
            if isinstance(declaration.type,
                    (c_ast.Struct, c_ast.Union, c_ast.IdentifierType)):
                fixed_decl = declaration
            else:
                print "reached else."
                fixed_decl = self._fix_decl_name_type(declaration, spec['type'])

            # Add the type name defined by typedef to a
            # symbol table (for usage in the lexer)
            #
            if typedef_namespace:
                if is_typedef:
                    self._add_typedef_name(fixed_decl.name, fixed_decl.coord)
                else:
                    self._add_identifier(fixed_decl.name, fixed_decl.coord)

            declarations.append(fixed_decl)

        return declarations

    def _build_function_definition(self, spec, decl, param_decls, body):
        """ Builds a function definition.
        """
        assert 'typedef' not in spec['storage']

        declaration = self._build_declarations(
            spec=spec,
            decls=[dict(decl=decl, init=None)],
            typedef_namespace=True)[0]

        return c_ast.FuncDef(
            decl=declaration,
            param_decls=param_decls,
            body=body,
            coord=decl.coord)

    def _select_struct_union_class(self, token):
        """ Given a token (either STRUCT or UNION), selects the
            appropriate AST class.
        """
        if token == 'struct':
            return c_ast.Struct
        else:
            return c_ast.Union

    ##
    ## Precedence and associativity of operators
    ##
    precedence = (
        ('left', 'LOR'),
        ('left', 'LAND'),
        ('left', 'OR'),
        ('left', 'XOR'),
        ('left', 'AND'),
        ('left', 'EQ', 'NE'),
        ('left', 'GT', 'GE', 'LT', 'LE'),
        ('left', 'RSHIFT', 'LSHIFT'),
        ('left', 'PLUS', 'MINUS'),
        ('left', 'TIMES', 'DIVIDE', 'MOD')
    )

    ##
    ## Grammar productions
    ## Implementation of the BNF defined in K&R2 A.13
    ##

    # Wrapper around a translation unit, to allow for empty input.
    # Not strictly part of the C99 Grammar, but useful in practice.
    #
    def p_translation_unit_or_empty(self, p):
        """ translation_unit_or_empty   : translation_unit
                                        | empty
        """
        print "HOLA"
        global counter
        if p[1] is None:
            p[0] = c_ast.FileAST([])
            self.graph.add_node(pydot.Node('node_'+str(counter), label='empty'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='translation_unit_or_empty'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
            p[0].ref = 'node_'+str(counter-1)
        else:
            x = p[1].pop()
            p[0] = c_ast.FileAST(p[1])
            self.graph.add_node(pydot.Node('node_'+str(counter), label='translation_unit_or_empty'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), x)
            self.graph.add_edge(edge)
            p[0].ref = 'node_'+str(counter-1)
        print "function-1: ", counter

    def p_translation_unit_1(self, p):
        """ translation_unit    : external_declaration
        """
        # Note: external_declaration is already a list
        #
        global counter
        p[0] = p[1]
        length = len(p[1]);
        self.graph.add_node(pydot.Node('node_'+str(counter), label='translation_unit'))
        counter = counter+1
        edge = pydot.Edge('node_'+str(counter-1), p[1][length-1])
        self.graph.add_edge(edge)
        p[0][length-1] = 'node_'+str(counter-1)
        print "function-2: ", counter

    def p_translation_unit_2(self, p):
        """ translation_unit    : translation_unit external_declaration
        """
        x = p[1].pop()
        y = p[2].pop()
        if p[2] is not None:
            p[1].extend(p[2])
        p[0] = p[1]
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='translation_unit'))
        counter = counter+1
        edge = pydot.Edge('node_'+str(counter-1), x)
        self.graph.add_edge(edge)
        edge = pydot.Edge('node_'+str(counter-1), y)
        self.graph.add_edge(edge)
        p[0].append('node_'+str(counter-1)) 
        print "function-3: ", counter
    # Declarations always come as lists (because they can be
    # several in one line), so we wrap the function definition
    # into a list as well, to make the return value of
    # external_declaration homogenous.
    #
    def p_external_declaration_1(self, p):
        """ external_declaration    : function_definition
        """
        global counter
        p[0] = [p[1]]
        self.graph.add_node(pydot.Node('node_'+str(counter), label='external_declaration'))
        counter = counter+1
        edge = pydot.Edge('node_'+str(counter-1), p[1].ref)
        self.graph.add_edge(edge)
        p[0].append('node_'+str(counter-1)) 
        print "function-4: ", counter

    def p_external_declaration_2(self, p):
        """ external_declaration    : declaration
        """
        global counter
        p[0] = p[1]

        length = len(p[1])
        self.graph.add_node(pydot.Node('node_'+str(counter), label='external_declaration'))
        counter = counter+1
        edge = pydot.Edge('node_'+str(counter-1), p[1][length-1])
        self.graph.add_edge(edge)
        p[0][length-1] = 'node_'+str(counter-1)
        print "function-5: ", counter

    def p_external_declaration_3(self, p):
        """ external_declaration    : pp_directive
                                    | pppragma_directive
        """
        p[0] = [p[1]]
        self._parse_error('Directives not supported yet',
                          self._coord(p.lineno(1)))
        print "function-6: ", counter

    def p_external_declaration_4(self, p):
        """ external_declaration    : SEMI
        """
        # print "HOQWEE"
        global counter
        p[0] = None
        self.graph.add_node(pydot.Node('node_'+str(counter), label='SEMI'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='external_declaration'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0] = ['@node_'+str(counter-1)];
        print "function-7: ", counter

    def p_pp_directive(self, p):
        """ pp_directive  : PPHASH
        """
        self._parse_error('Directives not supported yet',
                          self._coord(p.lineno(1)))
        print "function-8: ", counter

    def p_pppragma_directive(self, p):
        """ pppragma_directive      : PPPRAGMA
                                    | PPPRAGMA PPPRAGMASTR
        """
        global counter
        if len(p) == 3:
            p[0] = c_ast.Pragma(p[2], self._coord(p.lineno(2)))
            self.graph.add_node(pydot.Node('node_'+str(counter), label='PPPRAGMA'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='PPPRAGMASTR'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='pppragma_directive'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
            p[0].ref = 'node_'+str(counter-1)
        else:
            p[0] = c_ast.Pragma("", self._coord(p.lineno(1)))
            self.graph.add_node(pydot.Node('node_'+str(counter), label='PPPRAGMA'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='pppragma_directive'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
            p[0].ref = 'node_'+str(counter-1)
        print "function-9: ", counter

    # In function definitions, the declarator can be followed by
    # a declaration list, for old "K&R style" function definitios.
    #
    def p_function_definition_1(self, p):
        """ function_definition : declarator declaration_list_opt compound_statement
        """
        # no declaration specifiers - 'int' becomes the default type
        spec = dict(
            qual=[],
            storage=[],
            type=[c_ast.IdentifierType(['int'],
                                       coord=self._coord(p.lineno(1)))],
            function=[])

        global counter
        p[0] = self._build_function_definition(
            spec=spec,
            decl=p[1],
            param_decls=p[2],
            body=p[3])
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='function_definition'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge)
        if isinstance(p[2], list):
            length = len(p[2])
            edge = pydot.Edge("node_"+str(counter-1), p[2][length-1])
        elif isinstance(p[2], dict):
            edge = pydot.Edge("node_"+str(counter-1), p[2]["ref"])
        elif p[2] is not None:
            edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
        else:
            edge = pydot.Edge("node_"+str(counter-1), "empty")
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
        self.graph.add_edge(edge)
        p[0].ref = "node_" + str(counter-1)
        print "function-10: ", counter
        
        


    def p_function_definition_2(self, p):
        """ function_definition : declaration_specifiers declarator declaration_list_opt compound_statement
        """
        spec = p[1]


        p[0] = self._build_function_definition(
            spec=spec,
            decl=p[2],
            param_decls=p[3],
            body=p[4])
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='function_definition'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1]["ref"])
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
        self.graph.add_edge(edge)
        if isinstance(p[3], list):
            length = len(p[3])
            edge = pydot.Edge("node_"+str(counter-1), p[3][length-1])
        elif isinstance(p[3], dict):
            edge = pydot.Edge("node_"+str(counter-1), p[3]["ref"])
        elif p[3] is not None:
            edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
        else:
            edge = pydot.Edge("node_"+str(counter-1), "empty")
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), p[4].ref)
        self.graph.add_edge(edge)
        p[0].ref = "node_" + str(counter-1)
        print "function-11: ", counter

        print "fucntion definitoon 2", type(p[0]), type(p[1]), type(p[2]), type(p[3])
    def p_statement(self, p):
        """ statement   : labeled_statement
                        | expression_statement
                        | compound_statement
                        | selection_statement
                        | iteration_statement
                        | jump_statement
                        | pppragma_directive
        """
        # print "HOOOOLALAALALLALALALAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        p[0] = p[1]
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='statement'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge)
        p[0].ref = "node_" + str(counter-1)
    print "function-12: ", counter

        

    # In C, declarations can come several in a line:
    #   int x, *px, romulo = 5;
    #
    # However, for the AST, we will split them to separate Decl
    # nodes.
    #
    # This rule splits its declarations and always returns a list
    # of Decl nodes, even if it's one element long.
    #
    def p_decl_body(self, p):
        """ decl_body : declaration_specifiers init_declarator_list_opt
        """
        spec = p[1]
        print "hfggggggggggggggggggggggggggg", type(p[2]), p[2]

        # p[2] (init_declarator_list_opt) is either a list or None
        #
        if p[2] is None:
            # By the standard, you must have at least one declarator unless
            # declaring a structure tag, a union tag, or the members of an
            # enumeration.
            #
            ty = spec['type']
            s_u_or_e = (c_ast.Struct, c_ast.Union, c_ast.Enum)
            if len(ty) == 1 and isinstance(ty[0], s_u_or_e):
                decls = [c_ast.Decl(
                    name=None,
                    quals=spec['qual'],
                    storage=spec['storage'],
                    funcspec=spec['function'],
                    type=ty[0],
                    init=None,
                    bitsize=None,
                    coord=ty[0].coord)]

            # However, this case can also occur on redeclared identifiers in
            # an inner scope.  The trouble is that the redeclared type's name
            # gets grouped into declaration_specifiers; _build_declarations
            # compensates for this.
            #
            else:
                decls = self._build_declarations(
                    spec=spec,
                    decls=[dict(decl=None, init=None)],
                    typedef_namespace=True)
        

        else:
            decls = self._build_declarations(
                spec=spec,
                decls=p[2],
                typedef_namespace=True)

        p[0] = decls
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='decl_body'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1]["ref"])
        self.graph.add_edge(edge)
        if isinstance(p[2], list):
            length = len(p[2])
            edge = pydot.Edge("node_"+str(counter-1), p[2][length-1])
        elif isinstance(p[2], dict):
            edge = pydot.Edge("node_"+str(counter-1), p[2]["ref"])
        elif p[2] is not None:
            edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
        else:
            edge = pydot.Edge("node_"+str(counter-1), "empty")
        self.graph.add_edge(edge)
        p[0].append("node_" + str(counter-1))
        print "function-13: ", counter


    # The declaration has been split to a decl_body sub-rule and
    # SEMI, because having them in a single rule created a problem
    # for defining typedefs.
    #
    # If a typedef line was directly followed by a line using the
    # type defined with the typedef, the type would not be
    # recognized. This is because to reduce the declaration rule,
    # the parser's lookahead asked for the token after SEMI, which
    # was the type from the next line, and the lexer had no chance
    # to see the updated type symbol table.
    #
    # Splitting solves this problem, because after seeing SEMI,
    # the parser reduces decl_body, which actually adds the new
    # type into the table to be seen by the lexer before the next
    # line is reached.
    def p_declaration(self, p):
        """ declaration : decl_body SEMI
        """
        p[0] = p[1]
        global counter
        length = len(p[1])
        self.graph.add_node(pydot.Node('node_'+str(counter), label='SEMI'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='declaration'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1][length-1])
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_" + str(counter-2))
        self.graph.add_edge(edge)
        p[0].append("node_" + str(counter-1))
        print "function-14: ", counter
        
        
    # Since each declaration is a list of declarations, this
    # rule will combine all the declarations and return a single
    # list
    #
    def p_declaration_list(self, p):
        """ declaration_list    : declaration
                                | declaration_list declaration
        """
        p[0] = p[1] if len(p) == 2 else p[1] + p[2]
        global counter
        if len(p) == 2:
            length = len(p[1])
            self.graph.add_node(pydot.Node('node_'+str(counter), label='declaration_list'))
            counter = counter+1  
            edge = pydot.Edge("node_"+str(counter-1), p[1][length-1])
            self.graph.add_edge(edge)
        else:
            length = len(p[2])
            length1 = len(p[1])
            self.graph.add_node(pydot.Node('node_'+str(counter), label='declaration_list'))
            counter = counter+1  
            edge = pydot.Edge("node_"+str(counter-1), p[1][length1-1])
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), p[2][length-1])
            self.graph.add_edge(edge)
        p[0].append("node_" + str(counter-1))
        print "function-15: ", counter

    def p_declaration_specifiers_1(self, p):
        """ declaration_specifiers  : type_qualifier declaration_specifiers_opt
        """
        tmp_node1 = p[1].split("@")
        p[1] = tmp_node1[0]
        p[0] = self._add_declaration_specifier(p[2], p[1], 'qual')
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='declaration_specifiers'))
        counter = counter+1        
        edge = pydot.Edge("node_"+str(counter-1), tmp_node1[1])
        self.graph.add_edge(edge)
        if isinstance(p[2], list):
            length = len(p[2])
            edge = pydot.Edge("node_"+str(counter-1), p[2][length-1])
        elif isinstance(p[2], dict):
            edge = pydot.Edge("node_"+str(counter-1), p[2]["ref"])
        elif p[2] is not None:
            edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
        else:
            edge = pydot.Edge("node_"+str(counter-1), "empty")
        self.graph.add_edge(edge)
        p[0]["ref"] = "node_" + str(counter-1)
        print "function-16: ", counter

    def p_declaration_specifiers_2(self, p):
        """ declaration_specifiers  : type_specifier declaration_specifiers_opt
        """
        # tmp_node1 = p[1].split("@")
        # p[1] = tmp_node1[0]
        p[0] = self._add_declaration_specifier(p[2], p[1], 'type')
        global counter      
        self.graph.add_node(pydot.Node('node_'+str(counter), label='declaration_specifiers'))
        counter = counter+1        
        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge)
        if isinstance(p[2], list):
            length = len(p[2])
            edge = pydot.Edge("node_"+str(counter-1), p[2][length-1])
        elif isinstance(p[2], dict):
            edge = pydot.Edge("node_"+str(counter-1), p[2]["ref"])
        elif p[2] is not None:
            edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
        else:
            edge = pydot.Edge("node_"+str(counter-1), "empty")
        self.graph.add_edge(edge)
        p[0]["ref"] = "node_" + str(counter-1)
        print "function-17: ", counter

    def p_declaration_specifiers_3(self, p):
        """ declaration_specifiers  : storage_class_specifier declaration_specifiers_opt
        """
        tmp_node1 = p[1].split("@")
        p[1] = tmp_node1[0]
        p[0] = self._add_declaration_specifier(p[2], p[1], 'storage')
        global counter      
        self.graph.add_node(pydot.Node('node_'+str(counter), label='declaration_specifiers'))
        counter = counter+1        
        edge = pydot.Edge("node_"+str(counter-1), tmp_node1[1])
        self.graph.add_edge(edge)
        if isinstance(p[2], list):
            length = len(p[2])
            edge = pydot.Edge("node_"+str(counter-1), p[2][length-1])
        elif isinstance(p[2], dict):
            edge = pydot.Edge("node_"+str(counter-1), p[2]["ref"])
        elif p[2] is not None:
            edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
        else:
            edge = pydot.Edge("node_"+str(counter-1), "empty")
        self.graph.add_edge(edge)
        p[0]["ref"] = "node_" + str(counter-1)
        print "function-18: ", counter

    def p_declaration_specifiers_4(self, p):
        """ declaration_specifiers  : function_specifier declaration_specifiers_opt
        """
        tmp_node1 = p[1].split("@")
        p[1] = tmp_node1[0]
        p[0] = self._add_declaration_specifier(p[2], p[1], 'function')
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='declaration_specifiers'))
        counter = counter+1        
        edge = pydot.Edge("node_"+str(counter-1), tmp_node1[1])
        self.graph.add_edge(edge)
        if isinstance(p[2], list):
            length = len(p[2])
            edge = pydot.Edge("node_"+str(counter-1), p[2][length-1])
        elif isinstance(p[2], dict):
            edge = pydot.Edge("node_"+str(counter-1), p[2]["ref"])
        elif p[2] is not None:
            edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
        else:
            edge = pydot.Edge("node_"+str(counter-1), "empty")
        self.graph.add_edge(edge)
        p[0]["ref"] = "node_" + str(counter-1)
        print "function-19: ", counter

    def p_storage_class_specifier(self, p):
        """ storage_class_specifier : AUTO
                                    | REGISTER
                                    | STATIC
                                    | EXTERN
                                    | TYPEDEF
        """
        global counter
        p[0] = p[1]
        # print "TEST@@@@: ", p[1];
        if p[1] == 'auto':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='AUTO'))
        elif p[1] == 'register':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='REGISTER'))
        elif p[1] == 'static':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='STATIC'))
        elif p[1] == 'extern':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='EXTERN'))
        else:
            self.graph.add_node(pydot.Node('node_'+str(counter), label='TYPEDEF'))
        
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='storage_class_specifier'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0] = p[0] + '@node_' + str(counter-1)
        print "function-20: ", counter


    def p_function_specifier(self, p):
        """ function_specifier  : INLINE
        """
        global counter
        p[0] = p[1]
        self.graph.add_node(pydot.Node('node_'+str(counter), label='INLINE'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='function_specifier'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0] = p[0] + '@node_' + str(counter-1)
        print "function-21: ", counter


    def p_type_specifier_1(self, p):
        """ type_specifier  : VOID
                            | _BOOL
                            | CHAR
                            | SHORT
                            | INT
                            | LONG
                            | FLOAT
                            | DOUBLE
                            | _COMPLEX
                            | SIGNED
                            | UNSIGNED
                            | __INT128
        """
        global counter
        p[0] = c_ast.IdentifierType([p[1]], coord=self._coord(p.lineno(1)))
        if p[1] == 'void':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='VOID'))
        elif p[1] == '_Bool':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='_BOOL'))
        elif p[1] == 'char':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='CHAR'))
        elif p[1] == 'short':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='SHORT'))
        elif p[1] == 'int':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='INT'))
        elif p[1] == 'long':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='LONG'))
        elif p[1] == 'float':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='FLOAT'))
        elif p[1] == 'double':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='DOUBLE'))
        elif p[1] == '_Complex':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='_COMPLEX'))
        elif p[1] == 'signed':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='SIGNED'))
        elif p[1] == 'unsigned':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='UNSIGNED'))
        else:
            self.graph.add_node(pydot.Node('node_'+str(counter), label='_INT128'))

        counter = counter + 1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='type_specifier'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0].ref = "node_"+str(counter-1)
        # print "TEDSTKJJ ", p[1]
        print "function-22: ", counter

    def p_type_specifier_2(self, p):
        """ type_specifier  : typedef_name
                            | enum_specifier
                            | struct_or_union_specifier
        """
        global counter
        p[0] = p[1]
        self.graph.add_node(pydot.Node('node_'+str(counter), label='specifier'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge)
        p[0].ref = "node_"+str(counter-1)
        print "function-23: ", counter

    def p_type_qualifier(self, p):
        """ type_qualifier  : CONST
                            | RESTRICT
                            | VOLATILE
        """
        global counter
        p[0] = p[1]
        if p[1] == 'const':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='CONST'))
        elif p[1] == 'restrict':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='RESTRICT'))
        else:
            self.graph.add_node(pydot.Node('node_'+str(counter), label='VOLATILE'))
        counter = counter + 1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='type_qualifier'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0] = p[0] + '@node_' + str(counter-1)
        # print "TERSR ",p[1];
        print "function-24: ", counter

    def p_init_declarator_list_1(self, p):
        """ init_declarator_list    : init_declarator
                                    | init_declarator_list COMMA init_declarator
        """
        global counter
        p[0] = p[1] + [p[3]] if len(p) == 4 else [p[1]]
        if len(p) == 2:
            self.graph.add_node(pydot.Node('node_'+str(counter), label='init_declarator_list'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1]["ref"])
            self.graph.add_edge(edge)
            p[0].append("node_"+str(counter-1))
        else:
            length = len(p[1])
            self.graph.add_node(pydot.Node('node_'+str(counter), label='COMMA'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='init_declarator_list'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1][length-1])
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), p[3]["ref"])
            self.graph.add_edge(edge)
            p[0].append("node_"+str(counter-1))
        print "function-25: ", counter

    # If the code is declaring a variable that was declared a typedef in an
    # outer scope, yacc will think the name is part of declaration_specifiers,
    # not init_declarator, and will then get confused by EQUALS.  Pass None
    # up in place of declarator, and handle this at a higher level.
    #
    def p_init_declarator_list_2(self, p):
        """ init_declarator_list    : EQUALS initializer
        """
        p[0] = [dict(decl=None, init=p[2])]
        self.graph.add_node(pydot.Node('node_'+str(counter), label='EQUALS'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='init_declarator_list'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
        self.graph.add_edge(edge)
        p[0].append("node_"+str(counter-1))
        print "function-26: ", counter

    # Similarly, if the code contains duplicate typedefs of, for example,
    # array types, the array portion will appear as an abstract declarator.
    #
    def p_init_declarator_list_3(self, p):
        """ init_declarator_list    : abstract_declarator
        """
        p[0] = [dict(decl=p[1], init=None)]
        self.graph.add_node(pydot.Node('node_'+str(counter), label='init_declarator_list'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge)
        p[0].append("node_"+str(counter-1))
        print "function-27: ", counter

    # Returns a {decl=<declarator> : init=<initializer>} dictionary
    # If there's no initializer, uses None
    #
    def p_init_declarator(self, p):
        """ init_declarator : declarator
                            | declarator EQUALS initializer
        """
        global counter
        p[0] = dict(decl=p[1], init=(p[3] if len(p) > 2 else None))
        if len(p) == 2:
            self.graph.add_node(pydot.Node('node_'+str(counter), label='init_declarator'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge)
            p[0]["ref"] = "node_"+str(counter-1)
        else:
            self.graph.add_node(pydot.Node('node_'+str(counter), label='EQUALS'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='init_declarator'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
            self.graph.add_edge(edge)
            p[0]["ref"] = "node_"+str(counter-1)
        print "function-28: ", counter

    def p_specifier_qualifier_list_1(self, p):
        """ specifier_qualifier_list    : type_qualifier specifier_qualifier_list_opt
        """
        global counter
        tmp_node1 = p[1].split("@")
        p[1] = tmp_node1[0]
        p[0] = self._add_declaration_specifier(p[2], p[1], 'qual')
        self.graph.add_node(pydot.Node('node_'+str(counter), label='specifier_qualifier_list'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), tmp_node1[1])
        self.graph.add_edge(edge)
        if isinstance(p[2], list):
            length = len(p[2])
            edge = pydot.Edge("node_"+str(counter-1), p[2][length-1])
        elif isinstance(p[2], dict):
            edge = pydot.Edge("node_"+str(counter-1), p[2]["ref"])
        elif p[2] is not None:
            edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
        else:
            edge = pydot.Edge("node_"+str(counter-1), "empty")
        self.graph.add_edge(edge)
        p[0]["ref"] = "node_"+str(counter-1)
        print "function-29: ", counter

    def p_specifier_qualifier_list_2(self, p):
        """ specifier_qualifier_list    : type_specifier specifier_qualifier_list_opt
        """
        global counter
        p[0] = self._add_declaration_specifier(p[2], p[1], 'type')
        self.graph.add_node(pydot.Node('node_'+str(counter), label='specifier_qualifier_list'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge)
        if isinstance(p[2], list):
            length = len(p[2])
            edge = pydot.Edge("node_"+str(counter-1), p[2][length-1])
        elif isinstance(p[2], dict):
            edge = pydot.Edge("node_"+str(counter-1), p[2]["ref"])
        elif p[2] is not None:
            edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
        else:
            edge = pydot.Edge("node_"+str(counter-1), "empty")
        self.graph.add_edge(edge)
        p[0]["ref"] = "node_"+str(counter-1)
        print "function-30: ", counter

    # TYPEID is allowed here (and in other struct/enum related tag names), because
    # struct/enum tags reside in their own namespace and can be named the same as types
    #
    def p_struct_or_union_specifier_1(self, p):
        """ struct_or_union_specifier   : struct_or_union ID
                                        | struct_or_union TYPEID
        """
        global counter
        tmp_node1 = p[1].split("@")
        p[1] = tmp_node1[0]
        klass = self._select_struct_union_class(p[1])
        p[0] = klass(
            name=p[2],
            decls=None,
            coord=self._coord(p.lineno(2)))
        self.graph.add_node(pydot.Node('node_'+str(counter), label='TYPEID/ID'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='struct_or_union_specifier'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), tmp_node1[1])
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0].ref = "node_"+str(counter-1)
        print "function-31: ", counter
        

    def p_struct_or_union_specifier_2(self, p):
        """ struct_or_union_specifier : struct_or_union brace_open struct_declaration_list brace_close
        """
        global counter
        tmp_node1 = p[1].split("@")
        p[1] = tmp_node1[0]
        tmp_node2 = p[2].split("@")
        p[2] = tmp_node2[0]
        tmp_node3 = p[4].split("@")
        p[4] = tmp_node3[0]
        klass = self._select_struct_union_class(p[1])
        p[0] = klass(
            name=None,
            decls=p[3],
            coord=self._coord(p.lineno(2)))
        length = len(p[3])
        self.graph.add_node(pydot.Node('node_'+str(counter), label='struct_or_union_specifier'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), tmp_node1[1])
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), tmp_node2[1])
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), p[3][length-1])
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), tmp_node3[1])
        self.graph.add_edge(edge)
        p[0].ref = "node_"+str(counter-1)
        print "function-32: ", counter

    def p_struct_or_union_specifier_3(self, p):
        """ struct_or_union_specifier   : struct_or_union ID brace_open struct_declaration_list brace_close
                                        | struct_or_union TYPEID brace_open struct_declaration_list brace_close
        """
        global counter
        tmp_node1 = p[1].split("@")
        p[1] = tmp_node1[0]
        tmp_node2 = p[3].split("@")
        p[3] = tmp_node2[0]
        tmp_node3 = p[5].split("@")
        p[5] = tmp_node3[0]
        klass = self._select_struct_union_class(p[1])
        p[0] = klass(
            name=p[2],
            decls=p[4],
            coord=self._coord(p.lineno(2)))
        length = len(p[4])
    
        self.graph.add_node(pydot.Node('node_'+str(counter), label='ID/TYPEID'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='struct_or_union_specifier'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), tmp_node1[1])
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), tmp_node2[1])
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), p[4][length-1])
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), tmp_node3[1])
        self.graph.add_edge(edge)
        p[0].ref = "node_"+str(counter-1)
        print "function-33: ", counter


    def p_struct_or_union(self, p):
        """ struct_or_union : STRUCT
                            | UNION
        """
        global counter
        p[0] = p[1]
        if p[1] == 'struct':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='STRUCT'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='struct_or_union'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
            p[0] = p[0] + '@node_'+str(counter-1)
        else:
            self.graph.add_node(pydot.Node('node_'+str(counter), label='UNION'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='struct_or_union'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
            p[0] = p[0] + '@node_'+str(counter-1)
        print "function-34: ", counter

    # Combine all declarations into a single list
    #
    def p_struct_declaration_list(self, p):
        """ struct_declaration_list     : struct_declaration
                                        | struct_declaration_list struct_declaration
        """
        global counter
        if len(p) == 2:
            tmp_node = ''
            if len(p[1]) == 1:
                tmp_node = p[1][0]
                p[1] = None
            else:
                length = len(p[1])
                tmp_node = p[1][length-1]
            p[0] = p[1] or []
            self.graph.add_node(pydot.Node('node_'+str(counter), label='struct_declaration_list'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), tmp_node)
            self.graph.add_edge(edge)
            p[0].append('node_'+str(counter-1))
        else:
            tmp_node = ''
            if len(p[2]) == 1:
                tmp_node = p[2][0]
                p[2] = None
            else:
                length = len(p[2])
                tmp_node = p[2][length-1]
            x = p[1].pop()
            p[0] = p[1] + (p[2] or [])
            self.graph.add_node(pydot.Node('node_'+str(counter), label='struct_declaration_list'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), x)
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), tmp_node)
            self.graph.add_edge(edge)
            p[0].append('node_'+str(counter-1))
        print "function-35: ", counter
            

    def p_struct_declaration_1(self, p):
        """ struct_declaration : specifier_qualifier_list struct_declarator_list_opt SEMI
        """
        spec = p[1]
        assert 'typedef' not in spec['storage']

        if p[2] is not None:
            decls = self._build_declarations(
                spec=spec,
                decls=p[2])

        elif len(spec['type']) == 1:
            # Anonymous struct/union, gcc extension, C1x feature.
            # Although the standard only allows structs/unions here, I see no
            # reason to disallow other types since some compilers have typedefs
            # here, and pycparser isn't about rejecting all invalid code.
            #
            node = spec['type'][0]
            if isinstance(node, c_ast.Node):
                decl_type = node
            else:
                decl_type = c_ast.IdentifierType(node)

            decls = self._build_declarations(
                spec=spec,
                decls=[dict(decl=decl_type)])

        else:
            # Structure/union members can have the same names as typedefs.
            # The trouble is that the member's name gets grouped into
            # specifier_qualifier_list; _build_declarations compensates.
            #
            decls = self._build_declarations(
                spec=spec,
                decls=[dict(decl=None, init=None)])

        p[0] = decls
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='SEMI'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='struct_declaration'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1]["ref"])
        self.graph.add_edge(edge)
        if isinstance(p[2], list):
            length = len(p[2])
            edge = pydot.Edge("node_"+str(counter-1), p[2][length-1])
        elif isinstance(p[2], dict):
            edge = pydot.Edge("node_"+str(counter-1), p[2]["ref"])
        elif p[2] is not None:
            edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
        else:
            edge = pydot.Edge("node_"+str(counter-1), "empty")
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0].append('node_'+str(counter-1))
        print "function-36: ", counter

    def p_struct_declaration_2(self, p):
        """ struct_declaration : specifier_qualifier_list abstract_declarator SEMI
        """
        # "Abstract declarator?!", you ask?  Structure members can have the
        # same names as typedefs.  The trouble is that the member's name gets
        # grouped into specifier_qualifier_list, leaving any remainder to
        # appear as an abstract declarator, as in:
        #   typedef int Foo;
        #   struct { Foo Foo[3]; };
        #
        p[0] = self._build_declarations(
                spec=p[1],
                decls=[dict(decl=p[2], init=None)])
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='SEMI'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='struct_declaration'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1]["ref"])
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0].append('node_'+str(counter-1))
        print "function-37: ", counter

    def p_struct_declaration_3(self, p):
        """ struct_declaration : SEMI
        """
        global counter
        p[0] = None
        self.graph.add_node(pydot.Node('node_'+str(counter), label='SEMI'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='struct_declaration'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0] = ['node_'+str(counter-1)]
        print "function-38: ", counter

    def p_struct_declarator_list(self, p):
        """ struct_declarator_list  : struct_declarator
                                    | struct_declarator_list COMMA struct_declarator
        """
        global counter
        if len(p) == 4:
            p[0] = p[1] + [p[3]]
            length = len(p[1])
            self.graph.add_node(pydot.Node('node_'+str(counter), label='COMMA'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='struct_declarator_list'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1][length-1])
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), p[3]["ref"])
            self.graph.add_edge(edge)
            p[0].append("node_"+str(counter-1))
        else:
            p[0] = [p[1]]
            self.graph.add_node(pydot.Node('node_'+str(counter), label='struct_declarator_list'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1]["ref"])
            self.graph.add_edge(edge)
            p[0].append("node_"+str(counter-1))
            print "p[0]", p[0]

        # p[0] = p[1] + [p[3]] if len(p) == 4 else [p[1]]
        print "function-39: ", counter


    # struct_declarator passes up a dict with the keys: decl (for
    # the underlying declarator) and bitsize (for the bitsize)
    #
    def p_struct_declarator_1(self, p):
        """ struct_declarator : declarator
        """
        global counter
        p[0] = {'decl': p[1], 'bitsize': None}
        self.graph.add_node(pydot.Node('node_'+str(counter), label='struct_declarator'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge)
        p[0]["ref"] = "node_"+str(counter-1)
        print "function-40: ", counter

    def p_struct_declarator_2(self, p):
        """ struct_declarator   : declarator COLON constant_expression
                                | COLON constant_expression
        """
        global counter
        if len(p) > 3:
            p[0] = {'decl': p[1], 'bitsize': p[3]}
            self.graph.add_node(pydot.Node('node_'+str(counter), label='COLON'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='struct_declarator'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
            self.graph.add_edge(edge)
            p[0]["ref"] = "node_"+str(counter-1)
        else:
            p[0] = {'decl': c_ast.TypeDecl(None, None, None), 'bitsize': p[2]}
            self.graph.add_node(pydot.Node('node_'+str(counter), label='COLON'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='struct_declarator'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
            self.graph.add_edge(edge)
            p[0]["ref"] = "node_"+str(counter-1)
        print "function-41: ", counter

    def p_enum_specifier_1(self, p):
        """ enum_specifier  : ENUM ID
                            | ENUM TYPEID
        """
        global counter
        p[0] = c_ast.Enum(p[2], None, self._coord(p.lineno(1)))
        self.graph.add_node(pydot.Node('node_'+str(counter), label='ENUM'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='ID/TYPEID'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='enum_specifier'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0].ref = "node_"+str(counter-1)
        print "QWERTY: ", p[2]
        print "function-42: ", counter

    def p_enum_specifier_2(self, p):
        """ enum_specifier  : ENUM brace_open enumerator_list brace_close
        """
        p[0] = c_ast.Enum(None, p[3], self._coord(p.lineno(1)))
        global counter
        tmp_node1 = p[2].split("@")
        p[2] = tmp_node1[0]
        tmp_node2 = p[4].split("@")
        p[4] = tmp_node2[0]
        self.graph.add_node(pydot.Node('node_'+str(counter), label='ENUM'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='enum_specifier'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), tmp_node1[1]) 
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), tmp_node2[1]) 
        self.graph.add_edge(edge)
        p[0].ref = "node_" + str(counter-1)
        print "function-43: ", counter
        

    def p_enum_specifier_3(self, p):
        """ enum_specifier  : ENUM ID brace_open enumerator_list brace_close
                            | ENUM TYPEID brace_open enumerator_list brace_close
        """
        p[0] = c_ast.Enum(p[2], p[4], self._coord(p.lineno(1)))
        global counter
        tmp_node1 = p[3].split("@")
        p[3] = tmp_node1[0]
        tmp_node2 = p[5].split("@")
        p[5] = tmp_node2[0]
        
        self.graph.add_node(pydot.Node('node_'+str(counter), label='ENUM'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='ID / TYPEID'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='enum_specifier'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), tmp_node1[1]) 
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), tmp_node2[1]) 
        self.graph.add_edge(edge)
        p[0].ref = "node_" + str(counter-1)
        print "function-44: ", counter
                

    def p_enumerator_list(self, p):
        """ enumerator_list : enumerator
                            | enumerator_list COMMA
                            | enumerator_list COMMA enumerator
        """
        global counter
        if len(p) == 2:
            p[0] = c_ast.EnumeratorList([p[1]], p[1].coord)
            self.graph.add_node(pydot.Node('node_'+str(counter), label='enumerator_list'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge)
            
        elif len(p) == 3:
            p[0] = p[1]
            self.graph.add_node(pydot.Node('node_'+str(counter), label='COMMA'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='enumerator_list'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
            
        else:
            p[1].enumerators.append(p[3])
            p[0] = p[1]
            self.graph.add_node(pydot.Node('node_'+str(counter), label='COMMA'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='enumerator_list'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
            self.graph.add_edge(edge)
        p[0].ref = "node_" + str(counter-1)
        print "function-45: ", counter

            
    def p_enumerator(self, p):
        """ enumerator  : ID
                        | ID EQUALS constant_expression
        """
        global counter
        if len(p) == 2:
            enumerator = c_ast.Enumerator(
                        p[1], None,
                        self._coord(p.lineno(1)))
            self.graph.add_node(pydot.Node('node_'+str(counter), label='ID'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='enumerator'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)

        else:
            enumerator = c_ast.Enumerator(
                        p[1], p[3],
                        self._coord(p.lineno(1)))
            self.graph.add_node(pydot.Node('node_'+str(counter), label='ID'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='EQUALS'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='enumerator'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
            self.graph.add_edge(edge)

        self._add_identifier(enumerator.name, enumerator.coord)

        p[0] = enumerator
        p[0].ref = 'node_' + str(counter-1)
        print "function-46: ", counter

    def p_declarator_1(self, p):
        """ declarator  : direct_declarator
        """
        p[0] = p[1]
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='declarator'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge)
        p[0].ref = "node_" + str(counter-1)
        print "function-47: ", counter
        
    def p_declarator_2(self, p):
        """ declarator  : pointer direct_declarator
        """
        p[0] = self._type_modify_decl(p[2], p[1])
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='declarator'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
        self.graph.add_edge(edge)
        p[0].ref = "node_" + str(counter-1)
        print "function-48: ", counter
        
    # Since it's impossible for a type to be specified after a pointer, assume
    # it's intended to be the name for this declaration.  _add_identifier will
    # raise an error if this TYPEID can't be redeclared.
    #
    def p_declarator_3(self, p):
        """ declarator  : pointer TYPEID
        """
        decl = c_ast.TypeDecl(
            declname=p[2],
            type=None,
            quals=None,
            coord=self._coord(p.lineno(2)))

        p[0] = self._type_modify_decl(decl, p[1])
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='TYPEID'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='declarator'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0].ref = "node_" + str(counter-1)
        print "function-49: ", counter
        
    def p_direct_declarator_1(self, p):
        """ direct_declarator   : ID
        """
        global counter
        p[0] = c_ast.TypeDecl(
            declname=p[1],
            type=None,
            quals=None,
            coord=self._coord(p.lineno(1)))
        self.graph.add_node(pydot.Node('node_'+str(counter), label='ID'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='direct_declarator'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0].ref = 'node_' + str(counter-1)
        print "function-50: ", counter

    def p_direct_declarator_2(self, p):
        """ direct_declarator   : LPAREN declarator RPAREN
        """
        p[0] = p[2]
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='LPAREN'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='RPAREN'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='direct_declarator'))
        counter = counter+1
        
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0].ref = "node_" + str(counter-1)
        print "function-51: ", counter

    def p_direct_declarator_3(self, p):
        """ direct_declarator   : direct_declarator LBRACKET type_qualifier_list_opt assignment_expression_opt RBRACKET
        """
        quals = (p[3] if len(p) > 5 else []) or []
        # Accept dimension qualifiers
        # Per C99 6.7.5.3 p7
        arr = c_ast.ArrayDecl(
            type=None,
            dim=p[4] if len(p) > 5 else p[3],
            dim_quals=quals,
            coord=p[1].coord,
            ref = 'tmp')

        p[0] = self._type_modify_decl(decl=p[1], modifier=arr)
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='LBRACKET'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='RBRACKET'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='direct_declarator'))
        counter = counter+1

        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge)
        if isinstance(p[3], list):
            length = len(p[3])
            edge = pydot.Edge("node_"+str(counter-1), p[3][length-1])
        elif isinstance(p[3], dict):
            edge = pydot.Edge("node_"+str(counter-1), p[3]["ref"])
        elif p[3] is not None:
            edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
        else:
            edge = pydot.Edge("node_"+str(counter-1), "empty")
        self.graph.add_edge(edge)
        if isinstance(p[4], list):
            length = len(p[4])
            edge = pydot.Edge("node_"+str(counter-1), p[4][length-1])
        elif isinstance(p[4], dict):
            edge = pydot.Edge("node_"+str(counter-1), p[4]["ref"])
        elif p[4] is not None:
            edge = pydot.Edge("node_"+str(counter-1), p[4].ref)
        else:
            edge = pydot.Edge("node_"+str(counter-1), "empty")
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0].ref = "node_" + str(counter-1)
        print "function-52: ", counter
   

        
    def p_direct_declarator_4(self, p):
        """ direct_declarator   : direct_declarator LBRACKET STATIC type_qualifier_list_opt assignment_expression RBRACKET
                                | direct_declarator LBRACKET type_qualifier_list STATIC assignment_expression RBRACKET
        """
        # Using slice notation for PLY objects doesn't work in Python 3 for the
        # version of PLY embedded with pycparser; see PLY Google Code issue 30.
        # Work around that here by listing the two elements separately.
        listed_quals = [item if isinstance(item, list) else [item]
            for item in [p[3],p[4]]]
        dim_quals = [qual for sublist in listed_quals for qual in sublist
            if qual is not None]
        arr = c_ast.ArrayDecl(
            type=None,
            dim=p[5],
            dim_quals=dim_quals,
            coord=p[1].coord)

        p[0] = self._type_modify_decl(decl=p[1], modifier=arr)
        global counter
        if isinstance(p[3], str):
            self.graph.add_node(pydot.Node('node_'+str(counter), label='LBRACKET'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='STATIC'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='RBRACKET'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='direct_declarator'))
            counter = counter+1

            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-4))
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
            self.graph.add_edge(edge)
            if isinstance(p[4], list):
                length = len(p[4])
                edge = pydot.Edge("node_"+str(counter-1), p[4][length-1])
            elif isinstance(p[4], dict):
                edge = pydot.Edge("node_"+str(counter-1), p[4]["ref"])
            elif p[4] is not None:
                edge = pydot.Edge("node_"+str(counter-1), p[4].ref)
            else:
                edge = pydot.Edge("node_"+str(counter-1), "empty")
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), p[5].ref)
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
        else:
            self.graph.add_node(pydot.Node('node_'+str(counter), label='LBRACKET'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='STATIC'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='RBRACKET'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='direct_declarator'))
            counter = counter+1

            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-4))
            self.graph.add_edge(edge)
            length = len(p[3])
            edge = pydot.Edge("node_"+str(counter-1), p[3][length-1])
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), p[5].ref)
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
        p[0].ref = "node_" + str(counter-1)
        print "function-53: ", counter

    # Special for VLAs
    #
    def p_direct_declarator_5(self, p):
        """ direct_declarator   : direct_declarator LBRACKET type_qualifier_list_opt TIMES RBRACKET
        """
        arr = c_ast.ArrayDecl(
            type=None,
            dim=c_ast.ID(p[4], self._coord(p.lineno(4))),
            dim_quals=p[3] if p[3] != None else [],
            coord=p[1].coord)

        global counter
        p[0] = self._type_modify_decl(decl=p[1], modifier=arr)
        self.graph.add_node(pydot.Node('node_'+str(counter), label='LBRACKET'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='TIMES'))
        counter = counter+1    
        self.graph.add_node(pydot.Node('node_'+str(counter), label='RBRACKET'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='direct_declarator'))
        counter = counter+1

        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-4))
        self.graph.add_edge(edge)
        if isinstance(p[3], list):
            length = len(p[3])
            edge = pydot.Edge("node_"+str(counter-1), p[3][length-1])
        elif isinstance(p[3], dict):
            edge = pydot.Edge("node_"+str(counter-1), p[3]["ref"])
        elif p[3] is not None:
            edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
        else:
            edge = pydot.Edge("node_"+str(counter-1), "empty")
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0].ref = "node_"  + str(counter-1)
        print "function-54: ", counter
        
    def p_direct_declarator_6(self, p):
        """ direct_declarator   : direct_declarator LPAREN parameter_type_list RPAREN
                                | direct_declarator LPAREN identifier_list_opt RPAREN
        """
        func = c_ast.FuncDecl(
            args=p[3],
            type=None,
            coord=p[1].coord)
        global counter 

        # To see why _get_yacc_lookahead_token is needed, consider:
        #   typedef char TT;
        #   void foo(int TT) { TT = 10; }
        # Outside the function, TT is a typedef, but inside (starting and
        # ending with the braces) it's a parameter.  The trouble begins with
        # yacc's lookahead token.  We don't know if we're declaring or
        # defining a function until we see LBRACE, but if we wait for yacc to
        # trigger a rule on that token, then TT will have already been read
        # and incorrectly interpreted as TYPEID.  We need to add the
        # parameters to the scope the moment the lexer sees LBRACE.
        #
        if self._get_yacc_lookahead_token().type == "LBRACE":
            if func.args is not None:
                for param in func.args.params:
                    if isinstance(param, c_ast.EllipsisParam): break
                    self._add_identifier(param.name, param.coord)

        p[0] = self._type_modify_decl(decl=p[1], modifier=func)
        self.graph.add_node(pydot.Node('node_'+str(counter), label='LPAREN'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='RPAREN'))
        counter = counter+1    
        self.graph.add_node(pydot.Node('node_'+str(counter), label='direct_declarator'))
        counter = counter+1
        
        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge)
        if isinstance(p[3], list):
            length = len(p[3])
            edge = pydot.Edge("node_"+str(counter-1), p[3][length-1])
        elif isinstance(p[3], dict):
            edge = pydot.Edge("node_"+str(counter-1), p[3]["ref"])
        elif p[3] is not None:
            edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
        else:
            edge = pydot.Edge("node_"+str(counter-1), "empty")
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0].ref = "node_" + str(counter-1)
        print "function-55: ", counter
        

    def p_pointer(self, p):
        """ pointer : TIMES type_qualifier_list_opt
                    | TIMES type_qualifier_list_opt pointer
        """
        coord = self._coord(p.lineno(1))
        # Pointer decls nest from inside out. This is important when different
        # levels have different qualifiers. For example:
        #
        #  char * const * p;
        #
        # Means "pointer to const pointer to char"
        #
        # While:
        #
        #  char ** const p;
        #
        # Means "const pointer to pointer to char"
        #
        # So when we construct PtrDecl nestings, the leftmost pointer goes in
        # as the most nested type.
        nested_type = c_ast.PtrDecl(quals=p[2] or [], type=None, coord=coord)
        global counter
        if len(p) > 3:
            tail_type = p[3]
            while tail_type.type is not None:
                tail_type = tail_type.type
            tail_type.type = nested_type
            p[0] = p[3]
            self.graph.add_node(pydot.Node('node_'+str(counter), label='TIMES'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='pointer'))
            counter = counter+1

            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
            if isinstance(p[2], list):
                length = len(p[2])
                edge = pydot.Edge("node_"+str(counter-1), p[2][length-1])
            elif isinstance(p[2], dict):
                edge = pydot.Edge("node_"+str(counter-1), p[2]["ref"])
            elif p[2] is not None:
                edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
            else:
                edge = pydot.Edge("node_"+str(counter-1), "empty")
            self.graph.add_edge(edge)            
            edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
            self.graph.add_edge(edge)
            
        else:
            p[0] = nested_type
            self.graph.add_node(pydot.Node('node_'+str(counter), label='TIMES'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='pointer'))
            counter = counter+1

            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
            if isinstance(p[2], list):
                length = len(p[2])
                edge = pydot.Edge("node_"+str(counter-1), p[2][length-1])
            elif isinstance(p[2], dict):
                edge = pydot.Edge("node_"+str(counter-1), p[2]["ref"])
            elif p[2] is not None:
                edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
            else:
                edge = pydot.Edge("node_"+str(counter-1), "empty")
            self.graph.add_edge(edge)            

        p[0].ref = "node_" + str(counter-1)
        print "function-56: ", counter
        
    def p_type_qualifier_list(self, p):
        """ type_qualifier_list : type_qualifier
                                | type_qualifier_list type_qualifier
        """
        global counter
        if len(p) == 2:
            tmp_node = p[1].split("@")
            p[1] = tmp_node[0]
            p[0] = [p[1]]
            self.graph.add_node(pydot.Node('node_'+str(counter), label='type_qualifier_list'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), tmp_node[1])
            self.graph.add_edge(edge)
            p[0].append("node_"+str(counter-1))
        else:
            tmp_node = p[2].split("@")
            p[2] = tmp_node[0]
            x = p[1].pop()
            p[0] = p[1] + [p[2]]
            self.graph.add_node(pydot.Node('node_'+str(counter), label='type_qualifier_list'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), x)
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), tmp_node[1])
            self.graph.add_edge(edge)
            p[0].append("node_"+str(counter-1))
        p[0] = [p[1]] if len(p) == 2 else p[1] + [p[2]]
        print "function-57: ", counter

    def p_parameter_type_list(self, p):
        """ parameter_type_list : parameter_list
                                | parameter_list COMMA ELLIPSIS
        """
        if len(p) > 2:
            p[1].params.append(c_ast.EllipsisParam(self._coord(p.lineno(3))))

        p[0] = p[1]
        global counter
        if len(p) == 2:
            self.graph.add_node(pydot.Node('node_'+str(counter), label='parameter_type_list'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge)
            p[0].ref = "node_"+str(counter-1)
        else:
            self.graph.add_node(pydot.Node('node_'+str(counter), label='COMMA'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='ELLIPSIS'))
            counter = counter+1  
            self.graph.add_node(pydot.Node('node_'+str(counter), label='parameter_type_list'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
            p[0].ref = "node_"+str(counter-1)
        print "function-58: ", counter


    def p_parameter_list(self, p):
        """ parameter_list  : parameter_declaration
                            | parameter_list COMMA parameter_declaration
        """
        global counter
        if len(p) == 2: # single parameter
            p[0] = c_ast.ParamList([p[1]], p[1].coord)
            tmp_node = ''
            if isinstance(p[1], list):
                length = len(p[1])
                tmp_node = p[1][length-1]
            else:
                tmp_node = p[1].ref
            self.graph.add_node(pydot.Node('node_'+str(counter), label='parameter_list'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), tmp_node)
            self.graph.add_edge(edge)
            p[0].ref = "node_"+str(counter-1)

        else:
            p[1].params.append(p[3])
            p[0] = p[1]
            tmp_node = ''
            if isinstance(p[3], list):
                length = len(p[3])
                tmp_node = p[3][length-1]
            else:
                tmp_node = p[1].ref
            self.graph.add_node(pydot.Node('node_'+str(counter), label='COMMA'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='parameter_list'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            edge = pydot.Edge("node_"+str(counter-1), tmp_node)
            self.graph.add_edge(edge)
            p[0].ref = "node_"+str(counter-1)
        print "function-59: ", counter

    def p_parameter_declaration_1(self, p):
        """ parameter_declaration   : declaration_specifiers declarator
        """
        spec = p[1]
        if not spec['type']:
            spec['type'] = [c_ast.IdentifierType(['int'],
                coord=self._coord(p.lineno(1)))]
        print p[2], type(p[2])
        print "spec is: ", spec
        a = self._build_declarations(
            spec=spec,
            decls=[dict(decl=p[2])])
        print a
        p[0] = a[0]
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='parameter_declaration'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1]["ref"])
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
        self.graph.add_edge(edge) 
        p[0].ref = "node_"+str(counter-1)
        print "function-60: ", counter

    def p_parameter_declaration_2(self, p):
        """ parameter_declaration   : declaration_specifiers abstract_declarator_opt
        """
        spec = p[1]
        if not spec['type']:
            spec['type'] = [c_ast.IdentifierType(['int'],
                coord=self._coord(p.lineno(1)))]

        # Parameters can have the same names as typedefs.  The trouble is that
        # the parameter's name gets grouped into declaration_specifiers, making
        # it look like an old-style declaration; compensate.
        #
        global counter
        if len(spec['type']) > 1 and len(spec['type'][-1].names) == 1 and \
                self._is_type_in_scope(spec['type'][-1].names[0]):
            decl = self._build_declarations(
                    spec=spec,
                    decls=[dict(decl=p[2], init=None)])[0]
            p[0] = decl
            self.graph.add_node(pydot.Node('node_'+str(counter), label='parameter_declaration'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1]["ref"])
            self.graph.add_edge(edge)
            if isinstance(p[2], list):
                length = len(p[2])
                edge = pydot.Edge("node_"+str(counter-1), p[2][length-1])
            elif isinstance(p[2], dict):
                edge = pydot.Edge("node_"+str(counter-1), p[2]["ref"])
            elif p[2] is not None:
                edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
            else:
                edge = pydot.Edge("node_"+str(counter-1), "empty")
            self.graph.add_edge(edge) 
            p[0].append("node_"+str(counter-1))


        # This truly is an old-style parameter declaration
        #
        else:
            decl = c_ast.Typename(
                name='',
                quals=spec['qual'],
                type=p[2] or c_ast.TypeDecl(None, None, None),
                coord=self._coord(p.lineno(2)))
            typename = spec['type']
            decl = self._fix_decl_name_type(decl, typename)
            p[0] = decl
            self.graph.add_node(pydot.Node('node_'+str(counter), label='parameter_declaration'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1]["ref"])
            self.graph.add_edge(edge)
            if isinstance(p[2], list):
                length = len(p[2])
                edge = pydot.Edge("node_"+str(counter-1), p[2][length-1])
            elif isinstance(p[2], dict):
                edge = pydot.Edge("node_"+str(counter-1), p[2]["ref"])
            elif p[2] is not None:
                edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
            else:
                edge = pydot.Edge("node_"+str(counter-1), "empty")
            self.graph.add_edge(edge) 
            p[0].ref = "node_"+str(counter-1)
        print "function-61: ", counter

        

    def p_identifier_list(self, p):
        """ identifier_list : identifier
                            | identifier_list COMMA identifier
        """
        global counter
        if len(p) == 2: # single parameter
            p[0] = c_ast.ParamList([p[1]], p[1].coord)
            self.graph.add_node(pydot.Node('node_'+str(counter), label='identifier_list'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge) 
            p[0].ref = "node_"+str(counter-1)
        else:
            p[1].params.append(p[3])
            p[0] = p[1]
            self.graph.add_node(pydot.Node('node_'+str(counter), label='identifier_list'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='COMMA'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge) 
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
            self.graph.add_edge(edge) 
            p[0].ref = "node_"+str(counter-1)
        print "function-62: ", counter

    def p_initializer_1(self, p):
        """ initializer : assignment_expression
        """
        p[0] = p[1]
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='initializer'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge) 
        p[0].ref = "node_"+str(counter-1)           
        # print p[0]
        print "function-63: ", counter

    def p_initializer_2(self, p):
        """ initializer : brace_open initializer_list_opt brace_close
                        | brace_open initializer_list COMMA brace_close
        """
        if p[2] is None:
            p[0] = c_ast.InitList([], self._coord(p.lineno(1)))
        else:
            p[0] = p[2]

        global counter
        if len(p) == 4:
            tmp_node1 = p[1].split("@")
            p[1] = tmp_node1[0]
            tmp_node2 = p[3].split("@")
            p[3] = tmp_node2[0]
            self.graph.add_node(pydot.Node('node_'+str(counter), label='initializer'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), tmp_node1[1])
            self.graph.add_edge(edge)            
            if isinstance(p[2], list):
                length = len(p[2])
                edge = pydot.Edge("node_"+str(counter-1), p[2][length-1])
            elif isinstance(p[2], dict):
                edge = pydot.Edge("node_"+str(counter-1), p[2]["ref"])
            elif p[2] is not None:
                edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
            else:
                edge = pydot.Edge("node_"+str(counter-1), "empty")
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), tmp_node2[1])
            self.graph.add_edge(edge)
            p[0].ref = "node_"+str(counter-1)
        else:
            tmp_node1 = p[1].split("@")
            p[1] = tmp_node1[0]
            tmp_node2 = p[4].split("@")
            p[4] = tmp_node2[0]
            self.graph.add_node(pydot.Node('node_'+str(counter), label='COMMA'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='initializer'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), tmp_node1[1])
            self.graph.add_edge(edge) 
            edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
            self.graph.add_edge(edge)            
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), tmp_node2[1])
            self.graph.add_edge(edge)
            p[0].ref = "node_"+str(counter-1)
        print "function-64: ", counter

    def p_initializer_list(self, p):
        """ initializer_list    : designation_opt initializer
                                | initializer_list COMMA designation_opt initializer
        """
        global counter
        if len(p) == 3: # single initializer
            init = p[2] if p[1] is None else c_ast.NamedInitializer(p[1], p[2])
            p[0] = c_ast.InitList([init], p[2].coord)
            self.graph.add_node(pydot.Node('node_'+str(counter), label='initializer_list'))
            counter = counter+1

            if isinstance(p[1], list):
                length = len(p[1])
                edge = pydot.Edge("node_"+str(counter-1), p[1][length-1])
            elif isinstance(p[1], dict):
                edge = pydot.Edge("node_"+str(counter-1), p[1]["ref"])
            elif p[1] is not None:
                edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            else:
                edge = pydot.Edge("node_"+str(counter-1), "empty")
            self.graph.add_edge(edge)            
            edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
            self.graph.add_edge(edge)
            p[0].ref = "node_"+str(counter-1)
        else:
            init = p[4] if p[3] is None else c_ast.NamedInitializer(p[3], p[4])
            p[1].exprs.append(init)
            p[0] = p[1]
            self.graph.add_node(pydot.Node('node_'+str(counter), label='COMMA'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='initializer_list'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge)            
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)            
            if isinstance(p[3], list):
                length = len(p[3])
                edge = pydot.Edge("node_"+str(counter-1), p[3][length-1])
            elif isinstance(p[3], dict):
                edge = pydot.Edge("node_"+str(counter-1), p[3]["ref"])
            elif p[3] is not None:
                edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
            else:
                edge = pydot.Edge("node_"+str(counter-1), "empty")
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), p[4].ref)
            self.graph.add_edge(edge) 
            p[0].ref = "node_"+str(counter-1)
        print "function-65: ", counter


    def p_designation(self, p):
        """ designation : designator_list EQUALS
        """
        p[0] = p[1]
        global counter 
        self.graph.add_node(pydot.Node('node_'+str(counter), label='EQUALS'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1][length-1])
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0][length-1] = "node_"+str(counter-1)   
        print "function-66: ", counter                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              

    # Designators are represented as a list of nodes, in the order in which
    # they're written in the code.
    #
    def p_designator_list(self, p):                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         
        """ designator_list : designator
                            | designator_list designator
        """
        p[0] = [p[1]] if len(p) == 2 else p[1] + [p[2]]
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='designator_list'))
        counter = counter+1
        if len(p) == 3:
            length = len(p[1])
            edge = pydot.Edge("node_"+str(counter-1), p[1][length-1])
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
            self.graph.add_edge(edge)   
            p[0][length-1] = 'node_' + str(counter-1)
        else:
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge)            
            p[0].append('node_' + str(counter-1))
        print "function-67: ", counter

    def p_designator(self, p):
        """ designator  : LBRACKET constant_expression RBRACKET
                        | PERIOD identifier
        """
        p[0] = p[2]
        global counter
        if len(p) == 4:
            self.graph.add_node(pydot.Node('node_'+str(counter), label='LBRACKET'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='RBRACKET'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='designator'))
            counter = counter+1

            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
            self.graph.add_edge(edge)            
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
        else:
            self.graph.add_node(pydot.Node('node_'+str(counter), label='LBRACKET'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='designator'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
            self.graph.add_edge(edge)
        p[0].ref = 'node_' + str(counter-1)  
        print "function-68: ", counter      


    def p_type_name(self, p):
        """ type_name   : specifier_qualifier_list abstract_declarator_opt
        """
        typename = c_ast.Typename(
            name='',
            quals=p[1]['qual'],
            type=p[2] or c_ast.TypeDecl(None, None, None),
            coord=self._coord(p.lineno(2)))

        p[0] = self._fix_decl_name_type(typename, p[1]['type'])
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='type_name'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1]["ref"])
        self.graph.add_edge(edge)
        if isinstance(p[2], list):
            length = len(p[2])
            edge = pydot.Edge("node_"+str(counter-1), p[2][length-1])
        elif isinstance(p[2], dict):
            edge = pydot.Edge("node_"+str(counter-1), p[2]["ref"])
        elif p[2] is not None:
            edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
        else:
            edge = pydot.Edge("node_"+str(counter-1), "empty")
        self.graph.add_edge(edge)
        p[0].ref = "node_"+str(counter-1);
        # dictionary problems - specifier_qualifier_list is a dict
        print "function-69: ", counter


    def p_abstract_declarator_1(self, p):
        """ abstract_declarator     : pointer
        """
        dummytype = c_ast.TypeDecl(None, None, None)
        p[0] = self._type_modify_decl(
            decl=dummytype,
            modifier=p[1])
        print "qqqqqqqqqqqqqqqqqqqqqqqq", type(p[0])
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='abstract_declarator'))
        counter = counter+1

        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge)
        p[0].ref = 'node_' + str(counter-1)
        print "function-70: ", counter

    def p_abstract_declarator_2(self, p):
        """ abstract_declarator     : pointer direct_abstract_declarator
        """
        p[0] = self._type_modify_decl(p[2], p[1])
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='abstract_declarator'))
        counter = counter+1

        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
        self.graph.add_edge(edge)
        p[0].ref = 'node_' + str(counter-1)
        print "function-71: ", counter

    def p_abstract_declarator_3(self, p):
        """ abstract_declarator     : direct_abstract_declarator
        """
        p[0] = p[1]
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='abstract_declarator'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge)
        p[0].ref = 'node_' + str(counter-1)
        print "function-72: ", counter

    # Creating and using direct_abstract_declarator_opt here
    # instead of listing both direct_abstract_declarator and the
    # lack of it in the beginning of _1 and _2 caused two
    # shift/reduce errors.
    #
    def p_direct_abstract_declarator_1(self, p):
        """ direct_abstract_declarator  : LPAREN abstract_declarator RPAREN """
        p[0] = p[2]
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='LPAREN'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='RPAREN'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='direct_abstract_declarator'))
        counter = counter+1

        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0].ref = 'node_' + str(counter-1)
        print "function-73: ", counter

    def p_direct_abstract_declarator_2(self, p):
        """ direct_abstract_declarator  : direct_abstract_declarator LBRACKET assignment_expression_opt RBRACKET
        """
        arr = c_ast.ArrayDecl(
            type=None,
            dim=p[3],
            dim_quals=[],
            coord=p[1].coord)

        p[0] = self._type_modify_decl(decl=p[1], modifier=arr)
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='LBRACKET'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='RBRACKET'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='direct_abstract_declarator'))
        counter = counter+1

        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge)
        if isinstance(p[3], list):
            length = len(p[3])
            edge = pydot.Edge("node_"+str(counter-1), p[3][length-1])
        elif isinstance(p[3], dict):
            edge = pydot.Edge("node_"+str(counter-1), p[3]["ref"])
        elif p[3] is not None:
            edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
        else:
            edge = pydot.Edge("node_"+str(counter-1), "empty")
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0].ref = 'node_' + str(counter-1)
        print "function-74: ", counter


    def p_direct_abstract_declarator_3(self, p):
        """ direct_abstract_declarator  : LBRACKET assignment_expression_opt RBRACKET
        """
        p[0] = c_ast.ArrayDecl(
            type=c_ast.TypeDecl(None, None, None),
            dim=p[2],
            dim_quals=[],
            coord=self._coord(p.lineno(1)))
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='LBRACKET'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='RBRACKET'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='direct_abstract_declarator'))
        counter = counter+1

        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge)
        if isinstance(p[2], list):
            length = len(p[2])
            edge = pydot.Edge("node_"+str(counter-1), p[2][length-1])
        elif isinstance(p[2], dict):
            edge = pydot.Edge("node_"+str(counter-1), p[2]["ref"])
        elif p[2] is not None:
            edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
        else:
            edge = pydot.Edge("node_"+str(counter-1), "empty")
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0].ref = 'node_' + str(counter-1)
        print "function-75: ", counter


    def p_direct_abstract_declarator_4(self, p):
        """ direct_abstract_declarator  : direct_abstract_declarator LBRACKET TIMES RBRACKET
        """
        arr = c_ast.ArrayDecl(
            type=None,
            dim=c_ast.ID(p[3], self._coord(p.lineno(3))),
            dim_quals=[],
            coord=p[1].coord)

        p[0] = self._type_modify_decl(decl=p[1], modifier=arr)

        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='LBRACKET'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='TIMES'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='RBRACKET'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='direct_abstract_declarator'))
        counter = counter+1

        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-4))
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0].ref = 'node_' + str(counter-1)
        print "function-76: ", counter


    def p_direct_abstract_declarator_5(self, p):
        """ direct_abstract_declarator  : LBRACKET TIMES RBRACKET
        """
        global counter
        p[0] = c_ast.ArrayDecl(
            type=c_ast.TypeDecl(None, None, None),
            dim=c_ast.ID(p[3], self._coord(p.lineno(3))),
            dim_quals=[],
            coord=self._coord(p.lineno(1)))
        self.graph.add_node(pydot.Node('node_'+str(counter), label='LBRACKET'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='TIMES'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='RBRACKET'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='direct_abstract_declarator'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-4))
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0].ref = 'node_' + str(counter-1)
        print "function-77: ", counter

    def p_direct_abstract_declarator_6(self, p):
        """ direct_abstract_declarator  : direct_abstract_declarator LPAREN parameter_type_list_opt RPAREN
        """
        func = c_ast.FuncDecl(
            args=p[3],
            type=None,
            coord=p[1].coord)

        p[0] = self._type_modify_decl(decl=p[1], modifier=func)
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='LPAREN'))
        counter = counter+1 
        self.graph.add_node(pydot.Node('node_'+str(counter), label='RPAREN'))
        counter = counter+1 
        self.graph.add_node(pydot.Node('node_'+str(counter), label='direct_abstract_declarator'))
        counter = counter+1 


        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge)
        if isinstance(p[3], list):
            length = len(p[3])
            edge = pydot.Edge("node_"+str(counter-1), p[3][length-1])
        elif isinstance(p[3], dict):
            edge = pydot.Edge("node_"+str(counter-1), p[3]["ref"])
        elif p[3] is not None:
            edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
        else:
            edge = pydot.Edge("node_"+str(counter-1), "empty")
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0].ref = "node_" + str(counter-1)
        print "function-78: ", counter

    def p_direct_abstract_declarator_7(self, p):
        """ direct_abstract_declarator  : LPAREN parameter_type_list_opt RPAREN
        """
        p[0] = c_ast.FuncDecl(
            args=p[2],
            type=c_ast.TypeDecl(None, None, None),
            coord=self._coord(p.lineno(1)))
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='LPAREN'))
        counter = counter+1 
        self.graph.add_node(pydot.Node('node_'+str(counter), label='RPAREN'))
        counter = counter+1 
        self.graph.add_node(pydot.Node('node_'+str(counter), label='direct_abstract_declarator'))
        counter = counter+1 

        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge)
        if isinstance(p[2], list):
            length = len(p[2])
            edge = pydot.Edge("node_"+str(counter-1), p[2][length-1])
        elif isinstance(p[2], dict):
            edge = pydot.Edge("node_"+str(counter-1), p[2]["ref"])
        elif p[2] is not None:
            edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
        else:
            edge = pydot.Edge("node_"+str(counter-1), "empty")
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0].ref = "node_" + str(counter-1)
        print "function-79: ", counter


    # declaration is a list, statement isn't. To make it consistent, block_item
    # will always be a list
    #
    def p_block_item(self, p):
        """ block_item  : declaration
                        | statement
        """
        p[0] = p[1] if isinstance(p[1], list) else [p[1]]
        global counter
        if isinstance(p[1], list):
            length = len(p[1])
            self.graph.add_node(pydot.Node('node_'+str(counter), label='block_item'))
            counter = counter+1 
            edge = pydot.Edge("node_"+str(counter-1), p[1][length-1])
            self.graph.add_edge(edge)
            p[0][length-1] = "node_"+str(counter-1)
        else:
            self.graph.add_node(pydot.Node('node_'+str(counter), label='block_item'))
            counter = counter+1 
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge)
            p[0].append("node_"+str(counter-1))
        print "function-80: ", counter

    # Since we made block_item a list, this just combines lists
    #
    def p_block_item_list(self, p):
        """ block_item_list : block_item
                            | block_item_list block_item
        """
        # Empty block items (plain ';') produce [None], so ignore them
        global counter
        if len(p) == 2 or p[2] == [None]:
            p[0] = p[1]
            length = len(p[1])
            self.graph.add_node(pydot.Node('node_'+str(counter), label='block_item_list'))
            counter = counter+1 
            edge = pydot.Edge("node_"+str(counter-1), p[1][length-1])
            self.graph.add_edge(edge)
            p[0][length-1] = "node_"+str(counter-1)
        else:
            length1 = len(p[1])
            length2 = len(p[2])
            self.graph.add_node(pydot.Node('node_'+str(counter), label='block_item_list'))
            counter = counter+1 
            edge = pydot.Edge("node_"+str(counter-1), p[1][length1-1])
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), p[2][length2-1])
            self.graph.add_edge(edge)
            x = p[1].pop()
            p[0] = p[1] + p[2]
            length = len(p[0])
            p[0][length-1] = "node_"+str(counter-1)
        print "function-81: ", counter


    def p_compound_statement_1(self, p):
        """ compound_statement : brace_open block_item_list_opt brace_close """
        p[0] = c_ast.Compound(
            block_items=p[2],
            coord=self._coord(p.lineno(1)))
        tmp_node1 = p[1].split("@")
        p[1] = tmp_node1[0]
        tmp_node2 = p[3].split("@")
        p[3] = tmp_node2[0]
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='compound_statement'))
        counter = counter+1 
        edge = pydot.Edge("node_"+str(counter-1), tmp_node1[1])
        self.graph.add_edge(edge) 
        if isinstance(p[2], list):
            length = len(p[2])
            edge = pydot.Edge("node_"+str(counter-1), p[2][length-1])
        elif isinstance(p[2], dict):
            edge = pydot.Edge("node_"+str(counter-1), p[2]["ref"])
        elif p[2] is not None:
            edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
        else:
            edge = pydot.Edge("node_"+str(counter-1), "empty")
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), tmp_node2[1])
        self.graph.add_edge(edge) 
        p[0].ref = "node_"+str(counter-1)
        print "function-82: ", counter

    def p_labeled_statement_1(self, p):
        """ labeled_statement : ID COLON statement """
        p[0] = c_ast.Label(p[1], p[3], self._coord(p.lineno(1)))
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='ID'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='COLON'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='labeled_statement'))
        counter = counter+1 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
        self.graph.add_edge(edge) 
        p[0].ref = "node_"+str(counter-1)
        print "function-83: ", counter

    def p_labeled_statement_2(self, p):
        """ labeled_statement : CASE constant_expression COLON statement """
        p[0] = c_ast.Case(p[2], [p[4]], self._coord(p.lineno(1)))
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='CASE'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='COLON'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='labeled_statement'))
        counter = counter+1 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), p[4].ref)
        self.graph.add_edge(edge) 
        p[0].ref = "node_"+str(counter-1)
        print "function-84: ", counter

    def p_labeled_statement_3(self, p):
        """ labeled_statement : DEFAULT COLON statement """
        p[0] = c_ast.Default([p[3]], self._coord(p.lineno(1)))
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='DEFAULT'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='COLON'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='labeled_statement'))
        counter = counter+1 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
        self.graph.add_edge(edge) 
        p[0].ref = "node_"+str(counter-1)
        print "function-85: ", counter

    def p_selection_statement_1(self, p):
        """ selection_statement : IF LPAREN expression RPAREN statement """
        p[0] = c_ast.If(p[3], p[5], None, self._coord(p.lineno(1)))
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='IF'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='LPAREN'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='RPAREN'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='selection_statement'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-4))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), p[5].ref)
        self.graph.add_edge(edge) 
        p[0].ref = "node_"+str(counter-1)
        print "function-86: ", counter

    def p_selection_statement_2(self, p):
        """ selection_statement : IF LPAREN expression RPAREN statement ELSE statement """
        p[0] = c_ast.If(p[3], p[5], p[7], self._coord(p.lineno(1)))
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='IF'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='LPAREN'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='RPAREN'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='ELSE'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='selection_statement'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-5))
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-4))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), p[5].ref)
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), p[7].ref)
        self.graph.add_edge(edge) 
        p[0].ref = "node_"+str(counter-1)
        print "function-87: ", counter

    def p_selection_statement_3(self, p):
        """ selection_statement : SWITCH LPAREN expression RPAREN statement """
        p[0] = fix_switch_cases(
                c_ast.Switch(p[3], p[5], self._coord(p.lineno(1))))
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='SWITCH'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='LPAREN'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='RPAREN'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='selection_statement'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-4))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), p[5].ref)
        self.graph.add_edge(edge) 
        p[0].ref = "node_"+str(counter-1)
        print "function-88: ", counter

    def p_iteration_statement_1(self, p):
        """ iteration_statement : WHILE LPAREN expression RPAREN statement """
        p[0] = c_ast.While(p[3], p[5], self._coord(p.lineno(1)))
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='WHILE'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='LPAREN'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='RPAREN'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='iteration_statement'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-4))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), p[5].ref)
        self.graph.add_edge(edge) 
        p[0].ref = "node_"+str(counter-1)
        print "function-89: ", counter

    def p_iteration_statement_2(self, p):
        """ iteration_statement : DO statement WHILE LPAREN expression RPAREN SEMI """
        p[0] = c_ast.DoWhile(p[5], p[2], self._coord(p.lineno(1)))
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='DO'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='WHILE'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='LPAREN'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='RPAREN'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='SEMI'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='iteration_statement'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-6))
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-5))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-4))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), p[5].ref)
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge) 
        p[0].ref = "node_"+str(counter-1)
        print "function-90: ", counter

    def p_iteration_statement_3(self, p):
        """ iteration_statement : FOR LPAREN expression_opt SEMI expression_opt SEMI expression_opt RPAREN statement """
        p[0] = c_ast.For(p[3], p[5], p[7], p[9], self._coord(p.lineno(1)))
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='FOR'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='LPAREN'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='SEMI'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='SEMI'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='RPAREN'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='iteration_statement'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-6))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-5))
        self.graph.add_edge(edge)
        if isinstance(p[3], list):
            length = len(p[3])
            edge = pydot.Edge("node_"+str(counter-1), p[3][length-1])
        elif isinstance(p[3], dict):
            edge = pydot.Edge("node_"+str(counter-1), p[3]["ref"])
        elif p[3] is not None:
            edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
        else:
            edge = pydot.Edge("node_"+str(counter-1), "empty")
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-4))
        self.graph.add_edge(edge) 
        if isinstance(p[5], list):
            length = len(p[5])
            edge = pydot.Edge("node_"+str(counter-1), p[5][length-1])
        elif isinstance(p[5], dict):
            edge = pydot.Edge("node_"+str(counter-1), p[5]["ref"])
        elif p[5] is not None:
            edge = pydot.Edge("node_"+str(counter-1), p[5].ref)
        else:
            edge = pydot.Edge("node_"+str(counter-1), "empty")
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge) 
        if isinstance(p[7], list):
            length = len(p[7])
            edge = pydot.Edge("node_"+str(counter-1), p[7][length-1])
        elif isinstance(p[7], dict):
            edge = pydot.Edge("node_"+str(counter-1), p[7]["ref"])
        elif p[7] is not None:
            edge = pydot.Edge("node_"+str(counter-1), p[7].ref)
        else:
            edge = pydot.Edge("node_"+str(counter-1), "empty")
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), p[9].ref)
        self.graph.add_edge(edge) 
        p[0].ref = "node_"+str(counter-1)
        print "function-91: ", counter

    def p_iteration_statement_4(self, p):
        """ iteration_statement : FOR LPAREN declaration expression_opt SEMI expression_opt RPAREN statement """
        p[0] = c_ast.For(c_ast.DeclList(p[3], self._coord(p.lineno(1))),
                         p[4], p[6], p[8], self._coord(p.lineno(1)))
        length = len(p[3])
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='FOR'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='LPAREN'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='SEMI'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='RPAREN'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='iteration_statement'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-5))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-4))
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), p[3][length-1])
        self.graph.add_edge(edge) 
        if isinstance(p[4], list):
            length = len(p[4])
            edge = pydot.Edge("node_"+str(counter-1), p[4][length-1])
        elif isinstance(p[4], dict):
            edge = pydot.Edge("node_"+str(counter-1), p[4]["ref"])
        elif p[4] is not None:
            edge = pydot.Edge("node_"+str(counter-1), p[4].ref)
        else:
            edge = pydot.Edge("node_"+str(counter-1), "empty")
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge) 
        if isinstance(p[6], list):
            length = len(p[6])
            edge = pydot.Edge("node_"+str(counter-1), p[6][length-1])
        elif isinstance(p[6], dict):
            edge = pydot.Edge("node_"+str(counter-1), p[6]["ref"])
        elif p[6] is not None:
            edge = pydot.Edge("node_"+str(counter-1), p[6].ref)
        else:
            edge = pydot.Edge("node_"+str(counter-1), "empty")
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), p[8].ref)
        self.graph.add_edge(edge) 
        p[0].ref = "node_"+str(counter-1)
        print "function-92: ", counter

    def p_jump_statement_1(self, p):
        """ jump_statement  : GOTO ID SEMI """
        p[0] = c_ast.Goto(p[2], self._coord(p.lineno(1)))
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='GOTO'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='ID'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='SEMI'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='jump_statement'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-4))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge) 
        p[0].ref = "node_"+str(counter-1)
        print "function-93: ", counter

    def p_jump_statement_2(self, p):
        """ jump_statement  : BREAK SEMI """
        p[0] = c_ast.Break(self._coord(p.lineno(1)))
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='BREAK'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='SEMI'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='jump_statement'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge) 
        p[0].ref = "node_"+str(counter-1)
        print "function-94: ", counter

    def p_jump_statement_3(self, p):
        """ jump_statement  : CONTINUE SEMI """
        p[0] = c_ast.Continue(self._coord(p.lineno(1)))
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='CONTINUE'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='SEMI'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='jump_statement'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge) 
        p[0].ref = "node_"+str(counter-1)
        print "function-95: ", counter

    def p_jump_statement_4(self, p):
        """ jump_statement  : RETURN expression SEMI
                            | RETURN SEMI
        """
        p[0] = c_ast.Return(p[2] if len(p) == 4 else None, self._coord(p.lineno(1)))
        global counter
        if len(p) == 3:
            self.graph.add_node(pydot.Node('node_'+str(counter), label='RETURN'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='SEMI'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='jump_statement'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
            self.graph.add_edge(edge) 
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            p[0].ref = "node_"+str(counter-1)
        else:
            self.graph.add_node(pydot.Node('node_'+str(counter), label='RETURN'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='SEMI'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='jump_statement'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
            self.graph.add_edge(edge) 
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            p[0].ref = "node_"+str(counter-1)
        print "function-96: ", counter

    def p_expression_statement(self, p):
        """ expression_statement : expression_opt SEMI """
        global counter
        if p[1] is None:
            p[0] = c_ast.EmptyStatement(self._coord(p.lineno(2)))
            self.graph.add_node(pydot.Node('node_'+str(counter), label='SEMI'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='expression_statement'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            p[0].ref = "node_"+str(counter-1)
        else:
            p[0] = p[1]
            self.graph.add_node(pydot.Node('node_'+str(counter), label='SEMI'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='expression_statement'))
            counter = counter+1
            if isinstance(p[1], list):
                length = len(p[1])
                edge = pydot.Edge("node_"+str(counter-1), p[1][length-1])
            elif isinstance(p[1], dict):
                edge = pydot.Edge("node_"+str(counter-1), p[1]["ref"])
            elif p[1] is not None:
                edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            else:
                edge = pydot.Edge("node_"+str(counter-1), "empty")
            self.graph.add_edge(edge) 
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            p[0].ref = "node_"+str(counter-1)
        print "function-97: ", counter

    def p_expression(self, p):
        """ expression  : assignment_expression
                        | expression COMMA assignment_expression
        """
        global counter
        if len(p) == 2:
            p[0] = p[1]
            self.graph.add_node(pydot.Node('node_'+str(counter), label='expression'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge) 
            p[0].ref = "node_"+str(counter-1)
        else:
            if not isinstance(p[1], c_ast.ExprList):
                p[1] = c_ast.ExprList([p[1]], p[1].coord)

            p[1].exprs.append(p[3])
            p[0] = p[1]
            self.graph.add_node(pydot.Node('node_'+str(counter), label='COMMA'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='expression'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge) 
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
            self.graph.add_edge(edge) 
            p[0].ref = "node_"+str(counter-1)
        print "function-98: ", counter

    def p_typedef_name(self, p):
        """ typedef_name : TYPEID """
        p[0] = c_ast.IdentifierType([p[1]], coord=self._coord(p.lineno(1)))
        # print "fdgdfgsffd",p[1]
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='TYPEID'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='typedef_name'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge) 
        p[0].ref = "node_"+str(counter-1)
        print "function-99: ", counter

    def p_assignment_expression(self, p):
        """ assignment_expression   : conditional_expression
                                    | unary_expression assignment_operator assignment_expression
        """
        global counter
        if len(p) == 2:
            p[0] = p[1]
            self.graph.add_node(pydot.Node('node_'+str(counter), label='assignment_expression'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge) 
            p[0].ref = "node_"+str(counter-1)
        else:
            tmp_node = p[2].split("@")
            p[2] = tmp_node[0]
            p[0] = c_ast.Assignment(p[2], p[1], p[3], p[1].coord)
            self.graph.add_node(pydot.Node('node_'+str(counter), label='assignment_expression'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge) 
            edge = pydot.Edge("node_"+str(counter-1), tmp_node[1])
            self.graph.add_edge(edge) 
            edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
            self.graph.add_edge(edge) 
            p[0].ref = "node_"+str(counter-1)
        print "function-100: ", counter

    # K&R2 defines these as many separate rules, to encode
    # precedence and associativity. Why work hard ? I'll just use
    # the built in precedence/associativity specification feature
    # of PLY. (see precedence declaration above)
    #
    def p_assignment_operator(self, p):
        """ assignment_operator : EQUALS
                                | XOREQUAL
                                | TIMESEQUAL
                                | DIVEQUAL
                                | MODEQUAL
                                | PLUSEQUAL
                                | MINUSEQUAL
                                | LSHIFTEQUAL
                                | RSHIFTEQUAL
                                | ANDEQUAL
                                | OREQUAL
        """
        p[0] = p[1]
        # print "sssssssssss", p[1], type(p[1])
        global counter
        if p[1] == '=':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='EQUALS'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='assignment_operator'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            p[0] = p[0] + '@node_'+str(counter-1)
        elif p[1] == '^=':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='XOREQUAL'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='assignment_operator'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            p[0] = p[0] + '@node_'+str(counter-1)    
        elif p[1] == '*=':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='TIMESEQUAL'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='assignment_operator'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            p[0] = p[0] + '@node_'+str(counter-1)    
        elif p[1] == '/=':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='DIVEQUAL'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='assignment_operator'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            p[0] = p[0] + '@node_'+str(counter-1)    
        elif p[1] == '%=':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='MODEQUAL'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='assignment_operator'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            p[0] = p[0] + '@node_'+str(counter-1)    
        elif p[1] == '+=':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='PLUSEQUAL'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='assignment_operator'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            p[0] = p[0] + '@node_'+str(counter-1)    
        elif p[1] == '-=':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='MINUSEQUAL'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='assignment_operator'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            p[0] = p[0] + '@node_'+str(counter-1)    
        elif p[1] == '<<=':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='LSHIFTEQUAL'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='assignment_operator'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            p[0] = p[0] + '@node_'+str(counter-1)    
        elif p[1] == '>>=':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='RSHIFTEQUAL'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='assignment_operator'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            p[0] = p[0] + '@node_'+str(counter-1)    
        elif p[1] == '&=':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='ANDEQUAL'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='assignment_operator'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            p[0] = p[0] + '@node_'+str(counter-1)    
        else:
            self.graph.add_node(pydot.Node('node_'+str(counter), label='OREQUAL'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='assignment_operator'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            p[0] = p[0] + '@node_'+str(counter-1)  
        print "function-101: ", counter   
    

    def p_constant_expression(self, p):
        """ constant_expression : conditional_expression """
        p[0] = p[1]
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='constant_expression'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge) 
        p[0].ref = "node_"+str(counter-1)
        print "function-102: ", counter

    def p_conditional_expression(self, p):
        """ conditional_expression  : binary_expression
                                    | binary_expression CONDOP expression COLON conditional_expression
        """
        global counter
        if len(p) == 2:
            p[0] = p[1]
            self.graph.add_node(pydot.Node('node_'+str(counter), label='conditional_expression'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge) 
            p[0].ref = "node_"+str(counter-1)
        else:
            p[0] = c_ast.TernaryOp(p[1], p[3], p[5], p[1].coord)
            self.graph.add_node(pydot.Node('node_'+str(counter), label='CONDOP'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='COLON'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='conditional_expression'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
            self.graph.add_edge(edge)  
            edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            edge = pydot.Edge("node_"+str(counter-1), p[5].ref)
            self.graph.add_edge(edge) 
            p[0].ref = "node_"+str(counter-1)
        print "function-103: ", counter

    def p_binary_expression(self, p):
        """ binary_expression   : cast_expression
                                | binary_expression TIMES binary_expression
                                | binary_expression DIVIDE binary_expression
                                | binary_expression MOD binary_expression
                                | binary_expression PLUS binary_expression
                                | binary_expression MINUS binary_expression
                                | binary_expression RSHIFT binary_expression
                                | binary_expression LSHIFT binary_expression
                                | binary_expression LT binary_expression
                                | binary_expression LE binary_expression
                                | binary_expression GE binary_expression
                                | binary_expression GT binary_expression
                                | binary_expression EQ binary_expression
                                | binary_expression NE binary_expression
                                | binary_expression AND binary_expression
                                | binary_expression OR binary_expression
                                | binary_expression XOR binary_expression
                                | binary_expression LAND binary_expression
                                | binary_expression LOR binary_expression
        """
        global counter 
        if len(p) == 2:
            p[0] = p[1]
            self.graph.add_node(pydot.Node('node_'+str(counter), label='binary_expression'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge) 
            p[0].ref = "node_"+str(counter-1)

        else:
            p[0] = c_ast.BinaryOp(p[2], p[1], p[3], p[1].coord)
            if p[2] == '*':
                self.graph.add_node(pydot.Node('node_'+str(counter), label='TIMES'))
                counter = counter+1
                self.graph.add_node(pydot.Node('node_'+str(counter), label='binary_expression'))
                counter = counter+1
                edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
                self.graph.add_edge(edge)
                edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
                self.graph.add_edge(edge) 
                edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
                self.graph.add_edge(edge)
                p[0].ref = "node_"+str(counter-1)
            elif p[2] == '/':
                self.graph.add_node(pydot.Node('node_'+str(counter), label='DIVIDE'))
                counter = counter+1
                self.graph.add_node(pydot.Node('node_'+str(counter), label='binary_expression'))
                counter = counter+1
                edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
                self.graph.add_edge(edge)
                edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
                self.graph.add_edge(edge) 
                edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
                self.graph.add_edge(edge)
                p[0].ref = "node_"+str(counter-1)
            elif p[2] == '%':
                self.graph.add_node(pydot.Node('node_'+str(counter), label='MOD'))
                counter = counter+1
                self.graph.add_node(pydot.Node('node_'+str(counter), label='binary_expression'))
                counter = counter+1
                edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
                self.graph.add_edge(edge)
                edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
                self.graph.add_edge(edge) 
                edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
                self.graph.add_edge(edge)
                p[0].ref = "node_"+str(counter-1)
            elif p[2] == '+':
                self.graph.add_node(pydot.Node('node_'+str(counter), label='PLUS'))
                counter = counter+1
                self.graph.add_node(pydot.Node('node_'+str(counter), label='binary_expression'))
                counter = counter+1
                edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
                self.graph.add_edge(edge)
                edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
                self.graph.add_edge(edge) 
                edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
                self.graph.add_edge(edge)
                p[0].ref = "node_"+str(counter-1)
            elif p[2] == '-':
                self.graph.add_node(pydot.Node('node_'+str(counter), label='MINUS'))
                counter = counter+1
                self.graph.add_node(pydot.Node('node_'+str(counter), label='binary_expression'))
                counter = counter+1
                edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
                self.graph.add_edge(edge)
                edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
                self.graph.add_edge(edge) 
                edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
                self.graph.add_edge(edge)
                p[0].ref = "node_"+str(counter-1)
            elif p[2] == '>>':
                self.graph.add_node(pydot.Node('node_'+str(counter), label='RSHIFT'))
                counter = counter+1
                self.graph.add_node(pydot.Node('node_'+str(counter), label='binary_expression'))
                counter = counter+1
                edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
                self.graph.add_edge(edge)
                edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
                self.graph.add_edge(edge) 
                edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
                self.graph.add_edge(edge)
                p[0].ref = "node_"+str(counter-1)
            elif p[2] == '<<':
                self.graph.add_node(pydot.Node('node_'+str(counter), label='LSHIFT'))
                counter = counter+1
                self.graph.add_node(pydot.Node('node_'+str(counter), label='binary_expression'))
                counter = counter+1
                edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
                self.graph.add_edge(edge)
                edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
                self.graph.add_edge(edge) 
                edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
                self.graph.add_edge(edge)
                p[0].ref = "node_"+str(counter-1)
            elif p[2] == '<':
                self.graph.add_node(pydot.Node('node_'+str(counter), label='LT'))
                counter = counter+1
                self.graph.add_node(pydot.Node('node_'+str(counter), label='binary_expression'))
                counter = counter+1
                edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
                self.graph.add_edge(edge)
                edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
                self.graph.add_edge(edge) 
                edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
                self.graph.add_edge(edge)
                p[0].ref = "node_"+str(counter-1)
            elif p[2] == '<=':
                self.graph.add_node(pydot.Node('node_'+str(counter), label='LE'))
                counter = counter+1
                self.graph.add_node(pydot.Node('node_'+str(counter), label='binary_expression'))
                counter = counter+1
                edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
                self.graph.add_edge(edge)
                edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
                self.graph.add_edge(edge) 
                edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
                self.graph.add_edge(edge)
                p[0].ref = "node_"+str(counter-1)
            elif p[2] == '>=':
                self.graph.add_node(pydot.Node('node_'+str(counter), label='GE'))
                counter = counter+1
                self.graph.add_node(pydot.Node('node_'+str(counter), label='binary_expression'))
                counter = counter+1
                edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
                self.graph.add_edge(edge)
                edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
                self.graph.add_edge(edge) 
                edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
                self.graph.add_edge(edge)
                p[0].ref = "node_"+str(counter-1)
            elif p[2] == '>':
                self.graph.add_node(pydot.Node('node_'+str(counter), label='GT'))
                counter = counter+1
                self.graph.add_node(pydot.Node('node_'+str(counter), label='binary_expression'))
                counter = counter+1
                edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
                self.graph.add_edge(edge)
                edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
                self.graph.add_edge(edge) 
                edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
                self.graph.add_edge(edge)
                p[0].ref = "node_"+str(counter-1)
            elif p[2] == '==':
                self.graph.add_node(pydot.Node('node_'+str(counter), label='EQ'))
                counter = counter+1
                self.graph.add_node(pydot.Node('node_'+str(counter), label='binary_expression'))
                counter = counter+1
                edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
                self.graph.add_edge(edge)
                edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
                self.graph.add_edge(edge) 
                edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
                self.graph.add_edge(edge)
                p[0].ref = "node_"+str(counter-1)
            elif p[2] == '!=':
                self.graph.add_node(pydot.Node('node_'+str(counter), label='NE'))
                counter = counter+1
                self.graph.add_node(pydot.Node('node_'+str(counter), label='binary_expression'))
                counter = counter+1
                edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
                self.graph.add_edge(edge)
                edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
                self.graph.add_edge(edge) 
                edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
                self.graph.add_edge(edge)
                p[0].ref = "node_"+str(counter-1)
            elif p[2] == '&':
                self.graph.add_node(pydot.Node('node_'+str(counter), label='AND'))
                counter = counter+1
                self.graph.add_node(pydot.Node('node_'+str(counter), label='binary_expression'))
                counter = counter+1
                edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
                self.graph.add_edge(edge)
                edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
                self.graph.add_edge(edge) 
                edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
                self.graph.add_edge(edge)
                p[0].ref = "node_"+str(counter-1)
            elif p[2] == '|':
                self.graph.add_node(pydot.Node('node_'+str(counter), label='OR'))
                counter = counter+1
                self.graph.add_node(pydot.Node('node_'+str(counter), label='binary_expression'))
                counter = counter+1
                edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
                self.graph.add_edge(edge)
                edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
                self.graph.add_edge(edge) 
                edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
                self.graph.add_edge(edge)
                p[0].ref = "node_"+str(counter-1)
            elif p[2] == '^':
                self.graph.add_node(pydot.Node('node_'+str(counter), label='XOR'))
                counter = counter+1
                self.graph.add_node(pydot.Node('node_'+str(counter), label='binary_expression'))
                counter = counter+1
                edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
                self.graph.add_edge(edge)
                edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
                self.graph.add_edge(edge) 
                edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
                self.graph.add_edge(edge)
                p[0].ref = "node_"+str(counter-1)
            elif p[2] == '&&':
                self.graph.add_node(pydot.Node('node_'+str(counter), label='LAND'))
                counter = counter+1
                self.graph.add_node(pydot.Node('node_'+str(counter), label='binary_expression'))
                counter = counter+1
                edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
                self.graph.add_edge(edge)
                edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
                self.graph.add_edge(edge) 
                edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
                self.graph.add_edge(edge)
                p[0].ref = "node_"+str(counter-1)
            else:
                self.graph.add_node(pydot.Node('node_'+str(counter), label='LOR'))
                counter = counter+1
                self.graph.add_node(pydot.Node('node_'+str(counter), label='binary_expression'))
                counter = counter+1
                edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
                self.graph.add_edge(edge)
                edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
                self.graph.add_edge(edge) 
                edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
                self.graph.add_edge(edge)
                p[0].ref = "node_"+str(counter-1)
            print "function-104: ", counter

    def p_cast_expression_1(self, p):
        """ cast_expression : unary_expression """
        p[0] = p[1]
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='cast_expression'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge) 
        p[0].ref = "node_"+str(counter-1)
        print "function-105: ", counter

    def p_cast_expression_2(self, p):
        """ cast_expression : LPAREN type_name RPAREN cast_expression """
        p[0] = c_ast.Cast(p[2], p[4], self._coord(p.lineno(1)))
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='LPAREN'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='RPAREN'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='cast_expression'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), p[4].ref)
        self.graph.add_edge(edge) 
        p[0].ref = "node_"+str(counter-1)
        print "function-106: ", counter

    def p_unary_expression_1(self, p):
        """ unary_expression    : postfix_expression """
        p[0] = p[1]
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='unary_expression'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge) 
        p[0].ref = "node_"+str(counter-1)
        print "function-107: ", counter

    def p_unary_expression_2(self, p):
        """ unary_expression    : PLUSPLUS unary_expression
                                | MINUSMINUS unary_expression
                                | unary_operator cast_expression
        """
        tmp_node = []
        if p[1] <> "++":
            if p[1] <> "--":
                tmp_node = p[1].split("@")
                p[1] = tmp_node[0]
        p[0] = c_ast.UnaryOp(p[1], p[2], p[2].coord)
        global counter
        if p[1] == '++':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='PLUSPLUS'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='unary_expression'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
            self.graph.add_edge(edge) 
            p[0].ref = "node_"+str(counter-1)
        elif p[1] == '--':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='MINUSMINUS'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='unary_expression'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
            self.graph.add_edge(edge) 
            p[0].ref = "node_"+str(counter-1)
        else:
            self.graph.add_node(pydot.Node('node_'+str(counter), label='unary_expression'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), tmp_node[1])
            self.graph.add_edge(edge) 
            edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
            self.graph.add_edge(edge) 
            p[0].ref = "node_"+str(counter-1)  
        print "function-108: ", counter  

    def p_unary_expression_3(self, p):
        """ unary_expression    : SIZEOF unary_expression
                                | SIZEOF LPAREN type_name RPAREN
        """
        p[0] = c_ast.UnaryOp(
            p[1],
            p[2] if len(p) == 3 else p[3],
            self._coord(p.lineno(1)))
        global counter
        if len(p) == 3:
            self.graph.add_node(pydot.Node('node_'+str(counter), label='SIZEOF'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='unary_expression'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
            self.graph.add_edge(edge) 
            p[0].ref = "node_"+str(counter-1)
        else:
            self.graph.add_node(pydot.Node('node_'+str(counter), label='SIZEOF'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='LPAREN'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='RPAREN'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='unary_expression'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-4))
            self.graph.add_edge(edge) 
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
            self.graph.add_edge(edge) 
            edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
            self.graph.add_edge(edge) 
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            p[0].ref = "node_"+str(counter-1)
        print "function-109: ", counter

    def p_unary_operator(self, p):
        """ unary_operator  : AND
                            | TIMES
                            | PLUS
                            | MINUS
                            | NOT
                            | LNOT
        """
        p[0] = p[1]
        global counter
        if p[1] == '&':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='AND'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='unary_operator'))
            counter = counter+1    
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            p[0] = p[0] + '@node_'+str(counter-1)
        elif p[1] == '*':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='TIMES'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='unary_operator'))
            counter = counter+1    
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            p[0] = p[0] + '@node_'+str(counter-1)
        elif p[1] == '+':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='PLUS'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='unary_operator'))
            counter = counter+1    
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            p[0] = p[0] + '@node_'+str(counter-1)
        elif p[1] == '-':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='MINUS'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='unary_operator'))
            counter = counter+1    
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            p[0] = p[0] + '@node_'+str(counter-1)
        elif p[1] == '!':
            self.graph.add_node(pydot.Node('node_'+str(counter), label='NOT'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='unary_operator'))
            counter = counter+1    
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            p[0] = p[0] + '@node_'+str(counter-1)
        else:
            self.graph.add_node(pydot.Node('node_'+str(counter), label='LNOT'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='unary_operator'))
            counter = counter+1    
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            p[0] = p[0] + '@node_'+str(counter-1)
        print "function-110: ", counter

    def p_postfix_expression_1(self, p):
        """ postfix_expression  : primary_expression """
        p[0] = p[1]
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='postfix_expression'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge) 
        p[0].ref =  "node_"+str(counter-1)
        print "function-111: ", counter

    def p_postfix_expression_2(self, p):
        """ postfix_expression  : postfix_expression LBRACKET expression RBRACKET """
        p[0] = c_ast.ArrayRef(p[1], p[3], p[1].coord)
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='LBRACKET'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='RBRACKET'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='postfix_expression'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge) 
        p[0].ref =  "node_"+str(counter-1)
        print "function-112: ", counter

    def p_postfix_expression_3(self, p):
        """ postfix_expression  : postfix_expression LPAREN argument_expression_list RPAREN
                                | postfix_expression LPAREN RPAREN
        """
        p[0] = c_ast.FuncCall(p[1], p[3] if len(p) == 5 else None, p[1].coord)
        global counter 
        if len(p) == 4:
            self.graph.add_node(pydot.Node('node_'+str(counter), label='LPAREN'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='RPAREN'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='postfix_expression'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge) 
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
            self.graph.add_edge(edge) 
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            p[0].ref =  "node_"+str(counter-1)
        else:
            self.graph.add_node(pydot.Node('node_'+str(counter), label='LPAREN'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='RPAREN'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='postfix_expression'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge) 
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
            self.graph.add_edge(edge) 
            edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
            self.graph.add_edge(edge) 
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge) 
            p[0].ref =  "node_"+str(counter-1)
        print "function-113: ", counter

    def p_postfix_expression_4(self, p):
        """ postfix_expression  : postfix_expression PERIOD ID
                                | postfix_expression PERIOD TYPEID
                                | postfix_expression ARROW ID
                                | postfix_expression ARROW TYPEID
        """
        # print "tttttttttttttttttttttttttttt" , p[3], type(p[3])
        field = c_ast.ID(p[3], self._coord(p.lineno(3)))
        p[0] = c_ast.StructRef(p[1], p[2], field, p[1].coord)
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='PERIOD/ARROW'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='ID/TYPEID'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='postfix_expression'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge) 
        p[0].ref =  "node_"+str(counter-1)
        print "function-114: ", counter

    def p_postfix_expression_5(self, p):
        """ postfix_expression  : postfix_expression PLUSPLUS
                                | postfix_expression MINUSMINUS
        """
        p[0] = c_ast.UnaryOp('p' + p[2], p[1], p[1].coord)
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='INCREMENT / DECREMENT'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='postfix_expression'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge) 
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge) 
        p[0].ref =  "node_"+str(counter-1)
        print "function-115: ", counter
         

    def p_postfix_expression_6(self, p):
        """ postfix_expression  : LPAREN type_name RPAREN brace_open initializer_list brace_close
                                | LPAREN type_name RPAREN brace_open initializer_list COMMA brace_close
        """
        p[0] = c_ast.CompoundLiteral(p[2], p[5])
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='LPAREN'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='RPAREN'))
        counter = counter+1
        if len(p) ==  7:
            tmp_node1 = p[4].split("@")
            p[4] = tmp_node1[0]
            tmp_node2 = p[6].split("@")
            p[6] = tmp_node2[0]
            self.graph.add_node(pydot.Node('node_'+str(counter), label='postfix_expression'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
            self.graph.add_edge(edge)  
            edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
            self.graph.add_edge(edge)      
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)  
            edge = pydot.Edge("node_"+str(counter-1), tmp_node1[1])
            self.graph.add_edge(edge)      
            edge = pydot.Edge("node_"+str(counter-1), p[5].ref)
            self.graph.add_edge(edge)      
            edge = pydot.Edge("node_"+str(counter-1), tmp_node2[1])
            self.graph.add_edge(edge)      
        else:
            tmp_node1 = p[4].split("@")
            p[4] = tmp_node1[0]
            tmp_node2 = p[7].split("@")
            p[7] = tmp_node2[0]
            self.graph.add_node(pydot.Node('node_'+str(counter), label='COMMA'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='postfix_expression'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-4))
            self.graph.add_edge(edge)  
            edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
            self.graph.add_edge(edge)      
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
            self.graph.add_edge(edge)  
            edge = pydot.Edge("node_"+str(counter-1), tmp_node1[1])
            self.graph.add_edge(edge)      
            edge = pydot.Edge("node_"+str(counter-1), p[5].ref)
            self.graph.add_edge(edge)      
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)  
            edge = pydot.Edge("node_"+str(counter-1), tmp_node2[1])
            self.graph.add_edge(edge)  
        p[0].ref =  "node_"+str(counter-1)
        print "function-116: ", counter
        

    def p_primary_expression_1(self, p):
        """ primary_expression  : identifier """
        p[0] = p[1]
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='primary_expression'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge)      
        p[0].ref =  "node_"+str(counter-1)
        print "function-117: ", counter
        

    def p_primary_expression_2(self, p):
        """ primary_expression  : constant """
        p[0] = p[1]
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='primary_expression'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge)      
        p[0].ref =  "node_"+str(counter-1)
        print "function-118: ", counter
        
    def p_primary_expression_3(self, p):
        """ primary_expression  : unified_string_literal
                                | unified_wstring_literal
        """
        p[0] = p[1]
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='primary_expression'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
        self.graph.add_edge(edge)      
        p[0].ref =  "node_"+str(counter-1)
        print "function-119: ", counter
        

    def p_primary_expression_4(self, p):
        """ primary_expression  : LPAREN expression RPAREN """
        p[0] = p[2]
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='LPAREN'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='RPAREN'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='primary_expression'))
        counter = counter+1
        
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge)  
        edge = pydot.Edge("node_"+str(counter-1), p[2].ref)
        self.graph.add_edge(edge)      
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)  
        p[0].ref =  "node_"+str(counter-1)
        print "function-120: ", counter
        

    def p_primary_expression_5(self, p):
        """ primary_expression  : OFFSETOF LPAREN type_name COMMA offsetof_member_designator RPAREN
        """
        coord = self._coord(p.lineno(1))
        p[0] = c_ast.FuncCall(c_ast.ID(p[1], coord),
                              c_ast.ExprList([p[3], p[5]], coord),
                              coord)
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='OFFSETOF'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='LPAREN'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='COMMA'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='RPAREN'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='primary_expression'))
        counter = counter+1
                
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-5))
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-4))
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
        self.graph.add_edge(edge)      
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
        self.graph.add_edge(edge)  
        edge = pydot.Edge("node_"+str(counter-1), p[5].ref)
        self.graph.add_edge(edge)
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)

        p[0].ref =  "node_"+str(counter-1)
        print "function-121: ", counter



    def p_offsetof_member_designator(self, p):
        """ offsetof_member_designator : identifier
                                         | offsetof_member_designator PERIOD identifier
                                         | offsetof_member_designator LBRACKET expression RBRACKET
        """
        global counter
        if len(p) == 2:
            p[0] = p[1]
            # global counter
            self.graph.add_node(pydot.Node('node_'+str(counter), label='offsetof_member_designator'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge)
            p[0].ref =  "node_"+str(counter-1)

        elif len(p) == 4:
            field = c_ast.ID(p[3], self._coord(p.lineno(3)))
            p[0] = c_ast.StructRef(p[1], p[2], field, p[1].coord)
            # global counter
            self.graph.add_node(pydot.Node('node_'+str(counter), label='PERIOD'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='offsetof_member_designator'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
            self.graph.add_edge(edge)
            p[0].ref =  "node_"+str(counter-1)

        elif len(p) == 5:
            p[0] = c_ast.ArrayRef(p[1], p[3], p[1].coord)
            # global counter
            self.graph.add_node(pydot.Node('node_'+str(counter), label='LBRACKET'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='RBRACKET'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='offsetof_member_designator'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-3))
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
            p[0].ref =  "node_"+str(counter-1)

        else:
            raise NotImplementedError("Unexpected parsing state. len(p): %u" % len(p))
        print "function-122: ", counter

    def p_argument_expression_list(self, p):
        """ argument_expression_list    : assignment_expression
                                        | argument_expression_list COMMA assignment_expression
        """
        global counter
        if len(p) == 2: # single expr
            p[0] = c_ast.ExprList([p[1]], p[1].coord)
            # global counter
            self.graph.add_node(pydot.Node('node_'+str(counter), label='argument_expression_list'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge)
            p[0].ref =  "node_"+str(counter-1)
        else:
            p[1].exprs.append(p[3])
            p[0] = p[1]
            # global counter
            self.graph.add_node(pydot.Node('node_'+str(counter), label='COMMA'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='argument_expression_list'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), p[3].ref)
            self.graph.add_edge(edge)
            p[0].ref =  "node_"+str(counter-1)
        print "function-123: ", counter

    def p_identifier(self, p):
        """ identifier  : ID """
        p[0] = c_ast.ID(p[1], self._coord(p.lineno(1)))
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='ID'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='identifier'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0].ref =  "node_"+str(counter-1)
        # print "ABHISHEK: ",p[1]
        print "function-124: ", counter

    def p_constant_1(self, p):
        """ constant    : INT_CONST_DEC
                        | INT_CONST_OCT
                        | INT_CONST_HEX
                        | INT_CONST_BIN
        """
        p[0] = c_ast.Constant(
            'int', p[1], self._coord(p.lineno(1)))
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='INT_CONST'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='constant'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0].ref =  "node_"+str(counter-1)
        print "function-125: ", counter

    def p_constant_2(self, p):
        """ constant    : FLOAT_CONST
                        | HEX_FLOAT_CONST
        """
        p[0] = c_ast.Constant(
            'float', p[1], self._coord(p.lineno(1)))
        global counter
        self.graph.add_node(pydot.Node('node_'+str(counter), label='FLOAT/HEX_FLOAT_CONST'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='constant'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0].ref =  "node_"+str(counter-1)
        print "function-126: ", counter

    def p_constant_3(self, p):
        """ constant    : CHAR_CONST
                        | WCHAR_CONST
        """
        global counter
        p[0] = c_ast.Constant(
            'char', p[1], self._coord(p.lineno(1)))
        # print "char constant", type(p[1])
        self.graph.add_node(pydot.Node('node_'+str(counter), label='CHAR_CONST'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='constant'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        p[0].ref =  "node_"+str(counter-1)
        print "function-127: ", counter
    # The "unified" string and wstring literal rules are for supporting
    # concatenation of adjacent string literals.
    # I.e. "hello " "world" is seen by the C compiler as a single string literal
    # with the value "hello world"
    #
    def p_unified_string_literal(self, p):
        """ unified_string_literal  : STRING_LITERAL
                                    | unified_string_literal STRING_LITERAL
        """
        global counter
        if len(p) == 2: # single literal
            p[0] = c_ast.Constant(
                'string', p[1], self._coord(p.lineno(1)))
            self.graph.add_node(pydot.Node('node_'+str(counter), label='STRING_LITERAL'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='unified_string_literal'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
            p[0].ref =  "node_"+str(counter-1)
        else:
            p[1].value = p[1].value[:-1] + p[2][1:]
            p[0] = p[1]
            self.graph.add_node(pydot.Node('node_'+str(counter), label='STRING_LITERAL'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='unified_string_literal'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge)
            p[0].ref = "node_"+str(counter-1)
        print "function-128: ", counter
            

    def p_unified_wstring_literal(self, p):
        """ unified_wstring_literal : WSTRING_LITERAL
                                    | unified_wstring_literal WSTRING_LITERAL
        """
        global counter
        if len(p) == 2: # single literal
            p[0] = c_ast.Constant(
                'string', p[1], self._coord(p.lineno(1)))
            self.graph.add_node(pydot.Node('node_'+str(counter), label='WSTRING_LITERAL'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='unified_wstring_literal'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
            p[0].ref =  "node_"+str(counter-1)
        else:
            p[1].value = p[1].value.rstrip()[:-1] + p[2][2:]
            p[0] = p[1]
            self.graph.add_node(pydot.Node('node_'+str(counter), label='WSTRING_LITERAL'))
            counter = counter+1
            self.graph.add_node(pydot.Node('node_'+str(counter), label='unified_wstring_literal'))
            counter = counter+1
            edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
            self.graph.add_edge(edge)
            edge = pydot.Edge("node_"+str(counter-1), p[1].ref)
            self.graph.add_edge(edge)
            p[0].ref = "node_"+str(counter-1)
        print "function-129: ", counter
            

    def p_brace_open(self, p):
        """ brace_open  :   LBRACE
        """
        global counter
        p[0] = p[1]
        p.set_lineno(0, p.lineno(1))
        self.graph.add_node(pydot.Node('node_'+str(counter), label='LBRACE'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='brace_open'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        #print "right brace printing", p[1], type(p[1])
        p[0] = p[0] + "@node_" + str(counter-1)  
        print "function-130: ", counter  

    def p_brace_close(self, p):
        """ brace_close :   RBRACE
        """
        global counter
        p[0] = p[1]
        p.set_lineno(0, p.lineno(1))
        self.graph.add_node(pydot.Node('node_'+str(counter), label='RBRACE'))
        counter = counter+1
        self.graph.add_node(pydot.Node('node_'+str(counter), label='brace_close'))
        counter = counter+1
        edge = pydot.Edge("node_"+str(counter-1), "node_"+str(counter-2))
        self.graph.add_edge(edge)
        #print "right brace printing", p[1], type(p[1])
        p[0] = p[0] + "@node_" + str(counter-1)
        print "function-131: ", counter

    def p_empty(self, p):
        'empty : '
        p[0] = None

    def p_error(self, p):
        # If error recovery is added here in the future, make sure
        # _get_yacc_lookahead_token still works!
        #
        if p:
            self._parse_error(
                'before: %s' % p.value,
                self._coord(lineno=p.lineno,
                            column=self.clex.find_tok_column(p)))
        else:
            self._parse_error('At end of input', self.clex.filename)
        print "function-132: ", counter


#------------------------------------------------------------------------------
if __name__ == "__main__":
    import pprint
    import time, sys
    from __init__ import preprocess_file
    from __init__ import parse_file
    # from pycparser import preprocess_file
    graph = pydot.Dot(graph_type='digraph')
    t1 = time.time()
    parser = CParser(lex_optimize=False, yacc_debug=True, yacc_optimize=False, graph=graph)
    sys.stdout.write(str(time.time() - t1) + '\n')
    filename = 'test2.c'
    # text1 = preprocess_file(filename, cpp_path='cpp', cpp_args=r'-Iutils/fake_libc_include')
    with open(filename, 'rU') as f:
        text = f.read()

    buf = '''
        int (*k)(int);
    '''

    # set debuglevel to 2 for debugging
    t, graph_returned = parser.parse(text, filename, debuglevel=0)
    print "graph returned: ", graph_returned
    graph_returned.write_png('test2.png')
    # t.show()

    # ast = parse_file(filename, use_cpp=True,
    #         cpp_path='cpp',
    #         cpp_args=r'-Iutils/fake_libc_include')
    # ast.show(showcoord=True)
    # self.graph.write_png('test1.png')

