# vlx2mqtt

This project allows to connect a Velux KLF200 bridge to MQTT.

Supported position values are:
* 0-100 the position in percent
* UP move the shutter/blind up
* DOWN move the shutter/blind down
* STOP to stop the current motion

Additionally you receive the current position and the state of the script (connected or not)

This python script is used on the [docker image vlx2mqtt](https://hub.docker.com/repository/docker/phpmonkeys/vlx2mqtt).