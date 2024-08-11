#include <gtest/gtest.h>

#include "Utils.h"

class CombinedStopTokenTest : public ::testing::Test {
protected:
  std::stop_source source1;
  std::stop_source source2;
  CombinedStopToken<2> combinedToken;
  CombinedStopTokenTest()
      : combinedToken(source1.get_token(), source2.get_token()) {}
};

TEST_F(CombinedStopTokenTest, request_stop_on_first_token) {
  auto token = combinedToken.get_token();
  EXPECT_TRUE(token.stop_possible());
  source1.request_stop();
  EXPECT_TRUE(source1.stop_requested());
  EXPECT_TRUE(token.stop_requested());
}

TEST_F(CombinedStopTokenTest, request_stop_on_second_token) {
  auto token = combinedToken.get_token();
  EXPECT_TRUE(token.stop_possible());
  source2.request_stop();
  EXPECT_TRUE(source2.stop_requested());
  EXPECT_TRUE(token.stop_requested());
}

TEST_F(CombinedStopTokenTest, request_stop_on_both_tokens) {
  auto token = combinedToken.get_token();
  EXPECT_TRUE(token.stop_possible());
  source1.request_stop();
  source2.request_stop();
  EXPECT_TRUE(source1.stop_requested());
  EXPECT_TRUE(source2.stop_requested());
  EXPECT_TRUE(token.stop_requested());
}

TEST_F(CombinedStopTokenTest, three_stop_tokens_stop1) {
  std::stop_source source3;
  auto combinedToken3 = combineStopTokens(
      source1.get_token(), source2.get_token(), source3.get_token());

  auto token = combinedToken3.get_token();
  EXPECT_TRUE(token.stop_possible());
  source1.request_stop();
  EXPECT_TRUE(source1.stop_requested());
  EXPECT_TRUE(token.stop_requested());
}

TEST_F(CombinedStopTokenTest, three_stop_tokens_stop2) {
  std::stop_source source3;
  auto combinedToken3 = combineStopTokens(
      source1.get_token(), source2.get_token(), source3.get_token());

  auto token = combinedToken3.get_token();
  EXPECT_TRUE(token.stop_possible());
  source2.request_stop();
  EXPECT_TRUE(source2.stop_requested());
  EXPECT_TRUE(token.stop_requested());
}

TEST_F(CombinedStopTokenTest, three_stop_tokens_stop3) {
  std::stop_source source3;
  auto combinedToken3 = combineStopTokens(
      source1.get_token(), source2.get_token(), source3.get_token());

  auto token = combinedToken3.get_token();
  EXPECT_TRUE(token.stop_possible());
  source2.request_stop();
  EXPECT_TRUE(source2.stop_requested());
  EXPECT_TRUE(token.stop_requested());
}
