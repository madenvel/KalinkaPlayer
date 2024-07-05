CC = clang++
CFLAGS = -Wall -g -std=c++23 -fsanitize=address -I../
LDFLAGS = -lm -lasound -lpthread -lcurlpp -lcurl -lFLAC++ -lFLAC -lfmt -lgtest_main -lgtest -fsanitize=address

# List of source files
SRCS = $(wildcard *.cpp)
SRCS += ../AudioGraphHttpStream.cpp \
		../FlacStreamDecoder.cpp \
		../AudioFormat.cpp \
		../AudioGraphNode.cpp \
		../AlsaAudioEmitter.cpp \
		../AudioStreamSwitcher.cpp \
		../AudioPlayer.cpp \
		../StreamState.cpp \
		../StateMonitor.cpp \
		../Log.cpp

# List of object files
OBJS = $(SRCS:.cpp=.o)

# Name of the executable
TARGET = native_test

all: $(TARGET)
	./$(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) $(OBJS) -o $(TARGET) $(LDFLAGS)

%.o: %.cpp
	$(CC) $(CFLAGS) -c $< -o $@

clean:
	rm -f $(OBJS) $(TARGET)
	rm -f ${TARGET}
