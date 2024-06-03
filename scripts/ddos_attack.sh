#!/bin/bash
for i in $(seq 10); do curl http://192.168.124.2:80/time ; sleep 0.2 ; done
