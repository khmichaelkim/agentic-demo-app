#!/bin/bash

# Create Lambda layer with Python dependencies
echo "Creating Lambda layer with Python dependencies..."

# Create the directory structure
mkdir -p layers/dependencies/python/lib/python3.11/site-packages

# Install dependencies with platform compatibility
pip install aws-xray-sdk requests -t layers/dependencies/python/lib/python3.11/site-packages/ --platform linux_x86_64 --only-binary=:all: --upgrade

echo "Lambda layer created successfully!"
echo "Contents of layers/dependencies:"
ls -la layers/dependencies/
echo "Contents of site-packages:"
ls -la layers/dependencies/python/lib/python3.11/site-packages/