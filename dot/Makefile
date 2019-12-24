all: global_advisor_1_quals global_advisor_2_graphs global_advisor_3_weighted

%: %.dot
	dot -Tpng -o ../images/$@.png $<
