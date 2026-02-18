from flask import Flask, request, send_file, Response
from io import BytesIO
import requests
from PIL import Image, ImageDraw
import segno
import math
import os

app = Flask(__name__)

HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>Repo-Style QR Art (Mask Select + Conservative)</title>
<style>
  body { font-family: Arial, sans-serif; margin: 40px; }
  h1 { margin-bottom: 8px; }
  label { display:block;
