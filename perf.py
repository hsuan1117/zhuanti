#!/usr/bin/env python3

import time
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pyrad.client import Client
from pyrad.dictionary import Dictionary
import pyrad.packet

TOTAL = 10000
PARALLEL = 10
EACH = TOTAL // PARALLEL
SERVER = "radius-service"
SECRET = b"testing123"

def create_radius_client():
    """建立 RADIUS 客戶端"""
    client = Client(server=SERVER, secret=SECRET, dict=Dictionary("dictionary"))
    return client

def send_auth_request(client, username, password):
    """發送 RADIUS 認證請求"""
    try:
        # 建立認證封包
        req = client.CreateAuthPacket(code=pyrad.packet.AccessRequest,
                                      User_Name=username,
                                      User_Password=password)

        # 發送請求
        reply = client.SendPacket(req)
