cmake_minimum_required(VERSION 3.16)
project(guide CXX)
add_executable(guide_server server.cc)
add_executable(guide_client client.cc)
