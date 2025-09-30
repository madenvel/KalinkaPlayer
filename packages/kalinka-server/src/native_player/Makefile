all: python

%.o: %.cpp
	$(CC) $(CFLAGS) -c $< -o $@

clean:
	rm -rf *.o
	rm -rf *.so
	rm -rf build/*

python:
	python setup.py build_ext --inplace
