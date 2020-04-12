#!/usr/bin/env python3
# vim: tabstop=4 expandtab shiftwidth=4 softtabstop=4

import os
import sys
import signal
import logging
import configparser
import paho.mqtt.client as mqtt
import argparse
import asyncio
from pyvlx import Position, PyVLX, OpeningDevice
from pyvlx.log import PYVLXLOG

parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
                                 description='''glues between pyvlx and mqtt stuff''')
parser.add_argument('config_file', metavar="<config_file>", help="file with configuration")
args = parser.parse_args()

# read and parse config file
config = configparser.RawConfigParser()
config.read(args.config_file)
# [mqtt]
MQTT_HOST = config.get("mqtt", "host")
MQTT_PORT = config.getint("mqtt", "port")
MQTT_LOGIN = config.get("mqtt", "login", fallback=None)
MQTT_PASSWORD = config.get("mqtt", "password", fallback=None)
STATUSTOPIC = config.get("mqtt", "statustopic")
ROOT_TOPIC = config.get("mqtt", "roottopic")
# [velux]
VLX_HOST = config.get("velux", "host")
VLX_PW = config.get("velux", "password")
# [log]
VERBOSE = config.get("log", "verbose")
LOGFILE = config.get("log", "logfile", fallback=None)

APPNAME = "vlx2mqtt"

running = True
mqttConn = False
nodes = {}

# init logging 
LOGFORMAT = '%(asctime)-15s %(message)s'

if VERBOSE:
    loglevel = logging.DEBUG
else:
    loglevel = logging.INFO

if LOGFILE:
    logging.basicConfig(filename=LOGFILE, format=LOGFORMAT, level=loglevel)
else:
    logging.basicConfig(stream=sys.stdout, format=LOGFORMAT, level=loglevel)

logging.info("Starting " + APPNAME)
if VERBOSE:
    logging.info("DEBUG MODE")
else:
    logging.debug("INFO MODE")

PYVLXLOG.setLevel(logging.FATAL)
ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.FATAL)
PYVLXLOG.addHandler(ch)

# MQTT
MQTT_CLIENT_ID = APPNAME + "_%d" % os.getpid()
mqttc = mqtt.Client(MQTT_CLIENT_ID)


# 0: Connection successful
# 1: Connection refused - incorrect protocol version
# 2: Connection refused - invalid client identifier
# 3: Connection refused - server unavailable
# 4: Connection refused - bad username or password
# 5: Connection refused - not authorised
# 6-255: Currently unused.
def mqtt_on_connect(client, userdata, flags, return_code):
    global mqttConn
    # logging.debug("mqtt_on_connect return_code: " + str(return_code))
    if return_code == 0:
        logging.info("Connected to %s:%s", MQTT_HOST, MQTT_PORT)
        mqttc.publish(ROOT_TOPIC + '/' + STATUSTOPIC, "CONNECTED", retain=True)

        # register devices
        for node in pyvlx.nodes:
            if isinstance(node, OpeningDevice):
                logging.debug("Subscribing to %s" % (ROOT_TOPIC + '/' + node.name + '/set'))
                mqttc.subscribe(ROOT_TOPIC + '/' + node.name + '/set')
        mqttConn = True
    elif return_code == 1:
        logging.info("Connection refused - unacceptable protocol version")
        cleanup()
    elif return_code == 2:
        logging.info("Connection refused - identifier rejected")
        cleanup()
    elif return_code == 3:
        logging.info("Connection refused - server unavailable")
        logging.info("Retrying in 10 seconds")
        time.sleep(10)
    elif return_code == 4:
        logging.info("Connection refused - bad user name or password")
        cleanup()
    elif return_code == 5:
        logging.info("Connection refused - not authorised")
        cleanup()
    else:
        logging.warning("Something went wrong. RC:" + str(return_code))
        cleanup()


def mqtt_on_disconnect(mosq, obj, return_code):
    global mqttConn
    mqttConn = False
    if return_code == 0:
        logging.info("Clean disconnection")
    else:
        logging.info("Unexpected disconnection. Reconnecting in 5 seconds")
        # logging.debug("return_code: %s", return_code)
        time.sleep(5)


def mqtt_on_message(client, userdata, msg):
    global nodes
    # set OpeningDevice?
    logging.debug("got topic: %s" % (str(msg.topic)))
    for node in pyvlx.nodes:
        if ROOT_TOPIC + '/' + node.name + '/set' not in msg.topic:
            continue
        logging.debug("Setting %s to %s" % (node.name, str(msg.payload.decode("utf-8"))))
        nodes[node.name] = msg.payload.decode("utf-8")


def cleanup(signum, frame):
    global running
    running = False
    logging.info("Exiting on signal %d", signum)


# note: only subclasses of OpeningDevice get registered
async def vlx_cb(node):
    global mqttConn
    if not mqttConn:
        return
    logging.debug("%s at %d%%" % (node.name, node.position.position_percent))
    mqttc.publish(ROOT_TOPIC + "/" + node.name + "/position", node.position.position_percent, retain=False)


async def main(loop):
    global running
    global pyvlx
    
    logging.debug("klf200      : %s" % VLX_HOST)
    logging.debug("MQTT broker : %s" % MQTT_HOST)
    if MQTT_LOGIN:
        logging.debug("  port      : %s" % (str(MQTT_PORT)))
        logging.debug("  login     : %s" % MQTT_LOGIN)
    logging.debug("statustopic : %s" % (str(STATUSTOPIC)))
    logging.debug("roottopic : %s" % (str(ROOT_TOPIC)))

    pyvlx = PyVLX(host=VLX_HOST, password=VLX_PW, loop=loop)
    await pyvlx.load_nodes()

    logging.debug("vlx nodes   : %s" % (len(pyvlx.nodes)))
    for node in pyvlx.nodes:
        logging.debug("  %s" % node.name)

    # set login and password, if available
    if MQTT_LOGIN:
        mqttc.username_pw_set(MQTT_LOGIN, MQTT_PASSWORD)

    # Connect to the broker and enter the main loop
    result = mqttc.connect(MQTT_HOST, MQTT_PORT, 60)
    while result != 0:
        logging.info("Connection failed with error code %s. Retrying", result)
        await asyncio.sleep(10)
        result = mqttc.connect(MQTT_HOST, MQTT_PORT, 60)

    # Define callbacks
    mqttc.on_connect = mqtt_on_connect
    mqttc.on_message = mqtt_on_message
    mqttc.on_disconnect = mqtt_on_disconnect

    mqttc.loop_start()
    await asyncio.sleep(1)

    # register callbacks
    for node in pyvlx.nodes:
        if isinstance(node, OpeningDevice):
            node.register_device_updated_cb(vlx_cb)
            logging.debug("watching: %s" % node.name)

    while running:
        await asyncio.sleep(1)

        # see if we received some mqtt commands
        for name, value in nodes.items():
            if str(value) == "UP":
                logging.debug("%s is going up", name)
                nodes[name] = -1
                await pyvlx.nodes[name].open(wait_for_completion=False)
                continue
            if str(value) == "DOWN":
                logging.debug("%s is going down", name)
                nodes[name] = -1
                await pyvlx.nodes[name].close(wait_for_completion=False)
                continue
            if str(value) == "STOP":
                logging.debug("%s is stopped", name)
                nodes[name] = -1
                await pyvlx.nodes[name].stop(wait_for_completion=False)
                continue
            if int(float(value)) >= 0:
                logging.debug("setting %s to value %s" % (name, value))
                nodes[name] = -1  # mark executed
                await pyvlx.nodes[name].set_position(Position(position_percent=int(float(value))), wait_for_completion=False)

    logging.info("Disconnecting from broker")
    # Publish a retained message to state that this client is offline
    mqttc.publish(ROOT_TOPIC + '/' + STATUSTOPIC, "DISCONNECTED", retain=True)
    mqttc.disconnect()
    mqttc.loop_stop()

    await pyvlx.disconnect()


# Use the signal module to handle signals
signal.signal(signal.SIGTERM, cleanup)
signal.signal(signal.SIGINT, cleanup)

if __name__ == '__main__':
    # pylint: disable=invalid-name
    LOOP = asyncio.get_event_loop()

    pid = str(os.getpid())
    pidfile = "/tmp/vlx.pid"

    if os.path.isfile(pidfile):
        print("%s already exists, exiting" % pidfile)
        sys.exit()
    file = open(pidfile, 'w')
    file.write(pid)
    file.close()

    try:
        LOOP.run_until_complete(main(LOOP))
    except KeyboardInterrupt:
        logging.info("Interrupted by keypress")
    finally:
        os.unlink(pidfile)
    LOOP.close()
    sys.exit(0)
