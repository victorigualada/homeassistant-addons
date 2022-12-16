#!/bin/bash

# This is a script done by ftarolli in his NukiBridgeAddon project
# https://github.com/ftarolli/NukiBridgeAddon/blob/main/run.sh
# which I adapted for this project.

DIR=$HOME/.local/lib
FILE=nuki.yaml
SCAN_TIMEOUT_SEC=10
VERBOSE=0

function parse_yaml {
   local prefix=$2
   local s='[[:space:]]*' w='[a-zA-Z0-9_]*' fs=$(echo @|tr @ '\034')
   sed -ne "s|^\($s\):|\1|" \
        -e "s|^\($s\)\($w\)$s:$s[\"']\(.*\)[\"']$s\$|\1$fs\2$fs\3|p" \
        -e "s|^\($s\)\($w\)$s:$s\(.*\)$s\$|\1$fs\2$fs\3|p"  $1 |
   awk -F$fs '{
      indent = length($1)/2;
      vname[indent] = $2;
      for (i in vname) {if (i > indent) {delete vname[i]}}
      if (length($3) > 0) {
         vn=""; for (i=0; i<indent; i++) {vn=(vn)(vname[i])("_")}
         printf("%s%s%s=\"%s\"\n", "'$prefix'",vn, $2, $3);
      }
   }'
}

function clone_repo() {
  cd "$DIR" && git clone https://github.com/dauden1184/RaspiNukiBridge.git && cd RaspiNukiBridge && pip install -r requirements.txt
}

function generate_config() {
  echo "-----------------------------------------------------------"
  echo "checking configuration file..."
  echo "-----------------------------------------------------------"

  echo "generating configuration file..."

  python3 "$DIR/__main__.py" --generate-config > "$FILE"

  echo "config file created..."
  echo "-----------------------------------------------------------"
  eval $(parse_yaml $FILE "nuki_")
  echo ""
  echo "BRIDGE DATA:"
  echo "app_id: $nuki_server_app_id"
  echo "token: $nuki_server_token"
  echo ""
}

function find_mac_address() {
  echo "-----------------------------------------------------------"
  echo "checking mac address..."
  echo "-----------------------------------------------------------"

  echo "looking for mac address..."

  # Scan new devices for <timeout> seconds
  bluetoothctl --timeout "$SCAN_TIMEOUT_SEC" scan on

  # Grep bluetooth Nuki MAC address
  LOCK_MAC=$(bluetoothctl devices | grep Nuki | awk -F ' ' '{print $2}' | tail -n 1)
  echo ""
  echo "MAC address found: $LOCK_MAC"
  echo "-----------------------------------------------------------"
  echo ""
}

function pair_lock() {
  echo "-----------------------------------------------------------"
  echo "lock need to be paired. Starting pairing..."
  echo "-----------------------------------------------------------"
  if [ $VERBOSE -gt 0 ]; then
      python3 "$DIR/__main__.py" --pair "$LOCK_MAC" --config $FILE --verbose $VERBOSE
  else
      python3 "$DIR/__main__.py" --pair "$LOCK_MAC" --config $FILE
  fi

  eval $(parse_yaml $FILE "nuki_")

  echo "-----------------------------------------------------------"
  echo "lock successfully paired."
  echo "-----------------------------------------------------------"
}

function print_systemctl_config() {
  echo "-----------------------------------------------------------"
  echo "Copy the following content to /etc/systemd/system/nukibridge.service"
  echo "        [Unit]
        Description=Nuki bridge
        After=network-online.target

        [Service]
        Type=simple
        Restart=always
        RestartSec=1
        User=$USER
        WorkingDirectory=$DIR
        ExecStart=python .

        [Install]
        WantedBy=multi-user.target"
  echo ""
  echo ""
  echo "Then run: "
  echo "        sudo systemctl daemon-reload
        sudo systemctl enable nukibridge.service
        sudo systemctl start nukibridge.service"
}

clone_repo
generate_config
find_mac_address
pair_lock
print_systemctl_config
