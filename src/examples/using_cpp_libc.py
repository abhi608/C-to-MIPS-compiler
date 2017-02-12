#-----------------------------------------------------------------
# pycparser: using_cpp_libc.py
#
# Shows how to use the provided 'cpp' (on Windows, substitute for
# the 'real' cpp if you're on Linux/Unix) and "fake" libc includes
# to parse a file that includes standard C headers.
#
# Eli Bendersky [http://eli.thegreenplace.net]
# License: BSD
#-----------------------------------------------------------------
import sys

# This is not required if you've installed pycparser into
# your site-packages/ with setup.py
#
sys.path.extend(['.', '..', '../..', "../../.."])
import pydot
from pycparser import parse_file


if __name__ == "__main__":
	graph = pydot.Dot(graph_type='digraph')
	if len(sys.argv) > 1 and sys.argv[1] == "-f":
		filename  = sys.argv[2]
	else:
		filename = ""
		# filename = 'examples/c_files/text2.c'
	ast, graph_returned = parse_file(filename, use_cpp=True,
            cpp_path='cpp',
            cpp_args=r'-Iutils/fake_libc_include',
            graph=graph)
	if graph_returned is not None:
		graph_returned.write_png('test2.png')
	# ast.show(showcoord=True)
