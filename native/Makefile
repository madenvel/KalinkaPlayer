
CC = g++
CFLAGS = -Wall -g -O2 -std=c++20
LDFLAGS = -lm -lasound -lpthread -lcurlpp -lcurl -lFLAC++ -lFLAC

# List of source files
SRCS = test.cpp AudioPlayer.cpp BufferNode.cpp DataSource.cpp ProcessNode.cpp ThreadPool.cpp AudioFormat.cpp AlsaPlayNode.cpp FlacDecoder.cpp

# List of object files
OBJS = $(SRCS:.cpp=.o)

# Name of the executable
TARGET = test

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) $(OBJS) -o $(TARGET) $(LDFLAGS)

%.o: %.cpp
	$(CC) $(CFLAGS) -c $< -o $@

clean:
	rm -f $(OBJS) $(TARGET)
