#!/usr/bin/env python

import pygal

g1 = pygal.Bar(secondary_range=(0, 25))
g1.title = 'Table size & insert time'
g1.x_labels = ['no aggregation', '5', '20', '100', '200', '1000']
g1.add('Table size (MB)', [346, 146, 64, 43, 43, 53])
g1.add('Insert time (s)', [23, 18, 13, 10, 10, 10], secondary=True)
g1.render_to_file('images/tuple_overhead_1.svg')

g2 = pygal.StackedBar(secondary_range=(0, 75))
g2.title = 'Index creation'
g2.x_labels = ['no aggregation', '5', '20', '100', '200', '1000']
g2.add('Index (MB)', [214, 478, 478, 478, 478, 478])
g2.add('Table (MB)', [346, 146, 64, 43, 43, 53])
g2.add('Create time (s)', [5.2, 73, 70, 68, 69, 67], secondary=True)
g2.render_to_file('images/tuple_overhead_2.svg')

g3 = pygal.Bar(secondary_range=(0, 2.7))
g3.title = 'SELECT performance'
g3.x_labels = ['no aggregation', '5', '20', '100', '200', '1000']
g3.add('All row (s)', [2.2, 4, 2.6, 2, 2, 2])
g3.add('Single row (ms)', [1.4, 0.25, 0.3, 0.45, 0.7, 2.7], secondary=True)
g3.render_to_file('images/tuple_overhead_3.svg')

g4 = pygal.Bar(secondary_range=(0, 371))
g4.title = 'PoWA objects size'
g4.x_labels = ['Non aggregated', 'Aggregated']
g4.add('Table size (MB)', [2350, 138])
g4.add('Index size (MB)', [374, 0.9], secondary=True)
g4.render_to_file('images/tuple_overhead_4.svg')
