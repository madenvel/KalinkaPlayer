CC = g++
CFLAGS = -Wall -g -std=c++23 -fpermissive -fsanitize=address -I../
LDFLAGS = -lm -lasound -lpthread -lcurlpp -lcurl -lFLAC++ -lFLAC -lfmt -lspdlog -lgtest_main -lgmock -lgtest  -fsanitize=address
# List of source files
SRCS = $(wildcard *.cpp)
SRCS += ../AlsaAudioEmitter.cpp \
		../AudioGraphHttpStream.cpp \
		../AudioGraphNode.cpp \
		../AudioPlayer.cpp \
		../AudioSampleFormat.cpp \
		../AudioStreamSwitcher.cpp \
		../Buffer.cpp \
		../FlacStreamDecoder.cpp \
		../Log.cpp \
		../Mp3StreamDecoder.cpp \
		../PerfMon.cpp \
		../StateMonitor.cpp \
		../StreamState.cpp

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
