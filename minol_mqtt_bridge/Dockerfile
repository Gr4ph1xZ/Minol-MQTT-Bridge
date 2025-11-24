# Use the official playwright python image which includes browsers
FROM mcr.microsoft.com/playwright/python:v1.56.0-jammy-amd64

# Set working directory
WORKDIR /app

# Install python dependencies
# paho-mqtt for MQTT communication
# requests and beautifulsoup4 are needed by your connector
RUN pip install paho-mqtt requests beautifulsoup4 playwright==1.56.0

# Copy necessary files
COPY run.sh /app/
COPY minol_connector.py /app/
COPY main.py /app/

# Make the run script executable
RUN chmod a+x /app/run.sh

# Start the application
CMD [ "/app/run.sh" ]