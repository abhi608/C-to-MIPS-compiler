#-----------------------------------------------------------------
# plyparser.py
#
# PLYParser class and other utilites for simplifying programming
# parsers with PLY
#
# Eli Bendersky [http://eli.thegreenplace.net]
# License: BSD
#-----------------------------------------------------------------
tmp = -1;

class Coord(object):
    """ Coordinates of a syntactic element. Consists of:
            - File name
            - Line number
            - (optional) column number, for the Lexer
    """
    __slots__ = ('file', 'line', 'column', '__weakref__')
    def __init__(self, file, line, column=None):
        self.file = file
        self.line = line
        self.column = column

    def __str__(self):
        str = "%s:%s" % (self.file, self.line)
        if self.column: str += ":%s" % self.column
        return str


class ParseError(Exception): pass

import pydot 
class PLYParser(object):
    def _create_opt_rule(self, rulename, counter):
        """ Given a rule name, creates an optional ply.yacc rule
            for it. The name of the optional rule is
            <rulename>_opt
        """
        optname = rulename + '_opt'
        global tmp
        print "counter is: ", counter
        def optrule(self, p):
            global tmp
            p[0] = p[1]
            self.graph.add_node(pydot.Node('node_'+str(counter), label=optname))
            if isinstance(p[1], list):
                length = len(p[1])
                print "listtttttt" , p[1], type(p[1][length-1]), length
                edge = pydot.Edge("node_"+str(counter), p[1][length-1]) #p[1][length-1]
                p[0].append("node_" + str(counter))
                print "LISTTTTTTTTTT", p[0], type(p[0]), edge
                self.graph.add_edge(edge)

            elif isinstance(p[1], dict):
                print "is it a dictinary?", p[1]
                edge = pydot.Edge("node_"+str(counter), p[1]["ref"])
                p[0]["ref"] = "node_" + str(counter)
                self.graph.add_edge(edge)

            elif p[1] is not None:
                edge = pydot.Edge("node_"+str(counter), p[1].ref)
                p[0].ref = "node_" + str(counter)
                print "OBJECTTTTTTT"
                self.graph.add_edge(edge)

            else:
                self.graph.add_node(pydot.Node('node_'+str(tmp), label="Empty"))
                tmp = tmp-1;
                edge = pydot.Edge("node_"+str(counter), 'node_'+str(tmp+1))
                self.graph.add_edge(edge)

           # self.graph.add_edge(edge)
            print "LEFTTTTTTT"
        counter = counter + 1
        optrule.__doc__ = '%s : empty\n| %s' % (optname, rulename)
        optrule.__name__ = 'p_%s' % optname
        setattr(self.__class__, optrule.__name__, optrule)
        return counter

    def _coord(self, lineno, column=None):
        return Coord(
                file=self.clex.filename,
                line=lineno,
                column=column)

    def _parse_error(self, msg, coord):
        raise ParseError("%s: %s" % (coord, msg))
