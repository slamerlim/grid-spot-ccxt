cmake_minimum_required(VERSION 3.20)
project(grid_backtest VERSION 0.1 LANGUAGES CXX)

option(GRID_SANITIZE "Enable address and undefined behavior sanitizers" OFF)
set(GRID_PYTHON "" CACHE FILEPATH "Optional supported Python interpreter for exact reference parity")
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
find_package(OpenSSL 3 REQUIRED COMPONENTS Crypto)

add_library(grid_research numeric.cpp history.cpp market.cpp engine.cpp simulator.cpp portfolio.cpp study.cpp trace.cpp)
target_include_directories(grid_research PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(grid_research PUBLIC OpenSSL::Crypto)
target_compile_options(grid_research PUBLIC -Wall -Wextra -Wconversion -Wsign-conversion -Wshadow -Werror)
if(GRID_SANITIZE)
  target_compile_options(grid_research PUBLIC -fsanitize=address,undefined -fno-omit-frame-pointer)
  target_link_options(grid_research PUBLIC -fsanitize=address,undefined)
endif()

add_executable(grid_backtest main.cpp)
target_link_libraries(grid_backtest PRIVATE grid_research)
enable_testing()
add_executable(grid_backtest_tests tests.cpp)
target_link_libraries(grid_backtest_tests PRIVATE grid_research)
add_test(NAME grid_backtest_primitives COMMAND grid_backtest_tests)
add_executable(grid_portfolio_tests portfolio_tests.cpp)
target_link_libraries(grid_portfolio_tests PRIVATE grid_research)
add_test(NAME grid_backtest_portfolio COMMAND grid_portfolio_tests)
add_executable(grid_engine_tests engine_tests.cpp)
target_link_libraries(grid_engine_tests PRIVATE grid_research)
add_test(NAME grid_backtest_engine COMMAND grid_engine_tests)
if(GRID_PYTHON)
  add_test(NAME grid_backtest_study_integration
    COMMAND ${GRID_PYTHON} ${CMAKE_CURRENT_SOURCE_DIR}/study_tests.py $<TARGET_FILE:grid_backtest>)
  set_tests_properties(grid_backtest_study_integration PROPERTIES TIMEOUT 120)
  add_test(NAME grid_backtest_python_math_parity
    COMMAND ${GRID_PYTHON} -m grid.research.module1_reference --cpp $<TARGET_FILE:grid_backtest>)
  set_tests_properties(grid_backtest_python_math_parity PROPERTIES
    ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR}/../../src"
    TIMEOUT 120)
  add_test(NAME grid_backtest_python_lifecycle_parity
    COMMAND ${GRID_PYTHON} -m grid.research.module1_lifecycle --cpp $<TARGET_FILE:grid_backtest>)
  set_tests_properties(grid_backtest_python_lifecycle_parity PROPERTIES
    ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR}/../../src"
    TIMEOUT 120)
  add_test(NAME grid_backtest_python_bar_parity
    COMMAND ${GRID_PYTHON} -m grid.research.module1_bars --cpp $<TARGET_FILE:grid_backtest>)
  set_tests_properties(grid_backtest_python_bar_parity PROPERTIES
    ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR}/../../src"
    TIMEOUT 120)
  add_test(NAME grid_backtest_python_portfolio_nav_parity
    COMMAND ${GRID_PYTHON} -m grid.research.module1_portfolio --cpp $<TARGET_FILE:grid_portfolio_tests>)
  set_tests_properties(grid_backtest_python_portfolio_nav_parity PROPERTIES
    ENVIRONMENT "PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR}/../../src"
    TIMEOUT 120)
endif()
