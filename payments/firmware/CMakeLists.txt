# El orden de estas lineas lo exige ESP-IDF: project.cmake tiene que incluirse
# antes de declarar el proyecto.
cmake_minimum_required(VERSION 3.5)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)

project(FinCorp_POS VERSION 1.0.0)
