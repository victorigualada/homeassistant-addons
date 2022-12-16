import asyncio
import datetime
import hashlib
import logging
import struct
import hmac
import enum

import crc16
import nacl.utils
import nacl.secret
from nacl.bindings.crypto_box import crypto_box_beforenm
from bleak import BleakScanner, BleakClient

BLE_SMARTLOCK_PAIRING_SERVICE = "a92ee100-5501-11e4-916c-0800200c9a66"
BLE_SMARTLOCK_CHAR = "a92ee202-5501-11e4-916c-0800200c9a66"
BLE_SMARTLOCK_PAIRING_CHAR = 'a92ee101-5501-11e4-916c-0800200c9a66'

BLE_OPENER_PAIRING_SERVICE = "a92ae100-5501-11e4-916c-0800200c9a66"
BLE_OPENER_CHAR = "a92ae202-5501-11e4-916c-0800200c9a66"
BLE_OPENER_PAIRING_CHAR = 'a92ae101-5501-11e4-916c-0800200c9a66'


class BridgeType(enum.Enum):
    HW = 1
    SW = 2


class DeviceType(enum.Enum):
    SMARTLOCK_1_2 = 0
    OPENER = 2
    SMARTDOOR = 3
    SMARTLOCK_3 = 4


class DoorsensorState(enum.Enum):
    UNAVAILABLE = 0
    DEACTIVATED = 1
    DOOR_CLOSED = 2
    DOOR_OPENED = 3
    DOOR_STATE_UNKOWN = 4
    CALIBRATING = 5
    UNCALIBRATED = 16
    REMOVED = 240
    UNKOWN = 255


class StatusCode(enum.Enum):
    COMPLETED = 0
    ACCEPTED = 1


class NukiCommand(enum.Enum):
    REQUEST_DATA = 0x0001
    PUBLIC_KEY = 0x0003
    CHALLENGE = 0x0004
    AUTH_AUTHENTICATOR = 0x0005
    AUTH_DATA = 0x0006
    AUTH_ID = 0x0007
    KEYTURNER_STATES = 0x000C
    LOCK_ACTION = 0x000D
    STATUS = 0x000E
    ERROR_REPORT = 0x0012
    REQUEST_CONFIG = 0x0014
    CONFIG = 0x0015
    AUTH_ID_CONFIRM = 0x001E


class NukiState(enum.Enum):
    UNINITIALIZED = 0x00
    PAIRING_MODE = 0x01
    DOOR_MODE = 0x02
    CONTINUOUS_MODE = 0x03
    MAINTENANCE_MODE = 0x04


class LockState(enum.Enum):
    UNCALIBRATED = 0x00
    LOCKED = 0x01
    UNLOCKING = 0x02
    UNLOCKED = 0x03
    LOCKING = 0x04
    UNLATCHED = 0x05
    UNLOCKED_LOCK_N_GO = 0x06
    UNLATCHING = 0x07
    CALIBRATION = 0xFC
    BOOT_RUN = 0xFD
    MOTOR_BLOCKED = 0xFE
    UNDEFINED = 0xFF


class OpenerState(enum.Enum):
    UNCALIBRATED = 0x00
    LOCKED = 0x01
    RTO_ACTIVE = 0x03
    OPEN = 0x05
    OPENING = 0x07
    UNDEFINED = 0xFF


class NukiAction(enum.Enum):
    NONE = 0x00
    UNLOCK = 0x01
    LOCK = 0x02
    UNLATCH = 0x03
    LOCK_N_GO = 0x04
    LOCK_N_GO_UNLATCH = 0x05
    FULL_LOCK = 0x06
    FOB_ACTION_1 = 0x81
    FOB_ACTION_2 = 0x82
    FOB_ACTION_3 = 0x83


class NukiClientType(enum.Enum):
    APP = 0x00
    BRIDGE = 0x01
    FOB = 0x02
    KEYPAD = 0x03


logger = logging.getLogger("raspinukibridge")


class NukiManager:

    def __init__(self, name, app_id, adapter="hci0"):
        self.name = name
        self.app_id = app_id
        self.type_id = NukiClientType.BRIDGE
        self._newstate_callback = None

        self._adapter = adapter
        self._devices = {}
        self._scanner = BleakScanner(adapter=self._adapter)
        self._scanner.register_detection_callback(self._detected_ibeacon)

    @property
    def newstate_callback(self):
        return self._newstate_callback

    @newstate_callback.setter
    def newstate_callback(self, value):
        self._newstate_callback = value
        for device in self._devices.values():
            asyncio.get_event_loop().create_task(self.newstate_callback(device))

    async def nuki_newstate(self, nuki):
        if self.newstate_callback:
            await self.newstate_callback(nuki)

    def get_client(self, address, timeout=None):
        return BleakClient(address, adapter=self._adapter, timeout=timeout)

    def __getitem__(self, index):
        return list(self._devices.values())[index]

    def nuki_by_id(self, nuki_id):
        return next(nuki for nuki in self._devices.values() if nuki.config.get("id") == nuki_id)

    def add_nuki(self, nuki: 'Nuki'):
        nuki.manager = self
        self._devices[nuki.address] = nuki

    @property
    def device_list(self):
        return list(self._devices.values())

    async def start_scanning(self):
        logger.info("Start scanning")
        await self._scanner.start()

    async def stop_scanning(self):
        logger.info("Stop scanning")
        try:
            await self._scanner.stop()
        except:
            pass

    async def _detected_ibeacon(self, device, advertisement_data):
        if device.address in self._devices:
            manufacturer_data = advertisement_data.manufacturer_data[76]
            if manufacturer_data[0] != 0x02:
                # Ignore HomeKit advertisement
                return
            logger.info(f"Nuki: {device.address}, RSSI: {device.rssi} {advertisement_data}")
            tx_p = manufacturer_data[-1]
            nuki = self._devices[device.address]
            nuki.set_ble_device(device)
            nuki.rssi = device.rssi
            if not nuki.device_type:
                try:
                    await nuki.connect()  # this will force the identification of the device type
                except:
                    await self.start_scanning()
                    return
            if not nuki.last_state or tx_p & 0x1:
                await nuki.update_state()
            elif not nuki.config:
                await nuki.get_config()


class Nuki:

    def __init__(self, address, auth_id, nuki_public_key, bridge_public_key, bridge_private_key):
        self.address = address
        self.auth_id = auth_id
        self.nuki_public_key = nuki_public_key
        self.bridge_public_key = bridge_public_key
        self.bridge_private_key = bridge_private_key
        self.manager = None
        self.id = None
        self.name = None
        self.rssi = None
        self.last_state = None
        self.config = {}

        self._device_type = None
        self._pairing_handle = None
        self._client = None
        self._challenge_command = None
        self._pairing_callback = None
        self._command_timeout_task = None
        self._reset_opener_state_task = None
        self.retry = 3
        self.connection_timeout = 10
        self.command_timeout = 30

        self._BLE_CHAR = None
        self._BLE_PAIRING_CHAR = None

        if nuki_public_key and bridge_private_key:
            self._create_shared_key()

    @property
    def device_type(self):
        return self._device_type
    
    @device_type.setter
    def device_type(self, device_type: DeviceType):
        if device_type == DeviceType.OPENER:
            self._BLE_PAIRING_CHAR = BLE_OPENER_PAIRING_CHAR
            self._BLE_CHAR = BLE_OPENER_CHAR
        else:
            self._BLE_PAIRING_CHAR = BLE_SMARTLOCK_PAIRING_CHAR
            self._BLE_CHAR = BLE_SMARTLOCK_CHAR
        self._device_type = device_type
        logger.info(f"Device type: {self.device_type}")

    def _create_shared_key(self):
        self._shared_key = crypto_box_beforenm(self.nuki_public_key, self.bridge_private_key)
        self._box = nacl.secret.SecretBox(self._shared_key)

    @property
    def is_battery_critical(self):
        return bool(self.last_state["critical_battery_state"] & 1)

    @property
    def is_battery_charging(self):
        return bool(self.last_state["critical_battery_state"] & 2)

    @property
    def battery_percentage(self):
        return ((self.last_state["critical_battery_state"] & 252) >> 2) * 2

    @staticmethod
    def _prepare_command(cmd_code: int, payload=bytes()):
        message = cmd_code.to_bytes(2, "little") + payload
        crc = crc16.crc16xmodem(message, 0xffff).to_bytes(2, "little")
        message += crc
        return message

    def _encrypt_command(self, cmd_code: int, payload=bytes()):
        unencrypted = self.auth_id + self._prepare_command(cmd_code, payload)[:-2]
        crc = crc16.crc16xmodem(unencrypted, 0xffff).to_bytes(2, "little")
        unencrypted += crc
        nonce = nacl.utils.random(24)
        encrypted = self._box.encrypt(unencrypted, nonce)[24:]
        length = len(encrypted).to_bytes(2, "little")
        message = nonce + self.auth_id + length + encrypted
        return message

    def _decrypt_command(self, data):
        nonce = data[:24]
        auth_id, length = struct.unpack("<IH", data[24:30])
        encrypted = nonce + data[30:30 + length]
        decrypted = self._box.decrypt(encrypted)
        return decrypted[4:]

    async def _parse_command(self, data):
        command, = struct.unpack("<H", data[:2])
        command = NukiCommand(command)
        #crc = data[-2:]
        data = data[2:-2]
        logger.debug(f"Parsing command: {command}, data: {data}")

        if command == NukiCommand.CHALLENGE:
            return command, {"nonce": data}

        elif self.device_type != DeviceType.OPENER and command == NukiCommand.KEYTURNER_STATES:
            values = struct.unpack("<BBBHBBBBBHBBBBBBBH", data[:21])
            return command, {"nuki_state": NukiState(values[0]),
                             "lock_state": LockState(values[1]),
                             "trigger": values[2],
                             "current_time": datetime.datetime(values[3], values[4], values[5],
                                                               values[6], values[7], values[8]),
                             "timezone_offset": values[9],
                             "critical_battery_state": values[10],
                             "current_update_count": values[11],
                             "lock_n_go_timer": values[12],
                             "last_lock_action": NukiAction(values[13]),
                             "last_lock_action_trigger": values[14],
                             "last_lock_action_completion_status": values[15],
                             "door_sensor_state": DoorsensorState(values[16]),
                             "nightmode_active": values[17],
                             # "accessory_battery_state": values[18],  # It doesn't exist?
                             }
        elif self.device_type == DeviceType.OPENER and command == NukiCommand.KEYTURNER_STATES:
            values = struct.unpack("<BBBHBBBBBHBBBBBBBH", data[:21])
            return command, {"nuki_state": NukiState(values[0]),
                             "lock_state": OpenerState(values[1]),
                             "trigger": values[2],
                             "current_time": datetime.datetime(values[3], values[4], values[5],
                                                               values[6], values[7], values[8]),
                             "timezone_offset": values[9],
                             "critical_battery_state": values[10],
                             "current_update_count": values[11],
                             "ring_to_open_timer": values[12],
                             "last_lock_action": NukiAction(values[13]),
                             "last_lock_action_trigger": values[14],
                             "last_lock_action_completion_status": values[15],
                             "door_sensor_state": DoorsensorState(values[16]),
                             "nightmode_active": values[17],
                             # "accessory_battery_state": values[18],  # It doesn't exist?
                             }
        elif self.device_type != DeviceType.OPENER and command == NukiCommand.CONFIG:
            values = struct.unpack("<I32sffBBBBBHBBBBBhBBBBBBBBBBBBBBH", data[:74])
            return command, {"id": values[0],
                             "name": values[1].split(b"\x00")[0].decode(),
                             "latitude": values[2],
                             "longitude": values[3],
                             "auto_unlatch": values[4],
                             "pairing_enabled": values[5],
                             "button_enabled": values[6],
                             "led_enabled": values[7],
                             "led_brightness": values[8],
                             "current_time": datetime.datetime(values[9], values[10], values[11],
                                                               values[12], values[13], values[14]),
                             "timezone_offset": values[15],
                             "dst_mode": values[16],
                             "has_fob": values[17],
                             "fob_action_1": values[18],
                             "fob_action_2": values[19],
                             "fob_action_3": values[20],
                             "single_lock": values[21],
                             "advertising_mode": values[22],
                             "has_keypad": values[23],
                             "firmware_version": f"{values[24]}.{values[25]}.{values[26]}",
                             "hardware_revision": f"{values[27]}.{values[28]}",
                             "homekit_status": values[29],
                             "timezone_id": values[30],
                             }

        elif self.device_type == DeviceType.OPENER and command == NukiCommand.CONFIG:
            values = struct.unpack("<I32sffBBBBHBBBBBhBBBBBBBBBBBBBH", data[:72])
            return command, {"id": values[0],
                             "name": values[1].split(b"\x00")[0].decode(),
                             "latitude": values[2],
                             "longitude": values[3],
                             "auto_unlatch": values[4],
                             "pairing_enabled": values[5],
                             "button_enabled": values[6],
                             "led_enabled": values[7],
                             "current_time": datetime.datetime(values[8], values[9], values[10],
                                                               values[11], values[12], values[13]),
                             "timezone_offset": values[14],
                             "dst_mode": values[15],
                             "has_fob": values[16],
                             "fob_action_1": values[17],
                             "fob_action_2": values[18],
                             "fob_action_3": values[19],
                             "operating_mode": values[20],
                             "advertising_mode": values[21],
                             "has_keypad": values[22],
                             "firmware_version": f"{values[23]}.{values[24]}.{values[25]}",
                             "hardware_revision": f"{values[26]}.{values[27]}",
                             "timezone_id": values[28],
                             }

        elif command == NukiCommand.PUBLIC_KEY:
            return command, {"public_key": data}

        elif command == NukiCommand.AUTH_ID:
            values = struct.unpack("<32s4s16s32s", data[:84])
            return command, {"authenticator": values[0],
                             "auth_id": values[1],
                             "uuuid": values[2],
                             "nonce": values[3]}

        elif command == NukiCommand.STATUS:
            status, = struct.unpack('<B', data[:1])
            return command, {"status": StatusCode(status)}

        elif command == NukiCommand.ERROR_REPORT:
            data, _cmd = struct.unpack('<bH', data[:3])
            return command, data

        return None, None

    async def reset_opener_state(self):
        await asyncio.sleep(30)
        self.last_state["last_lock_action_completion_status"] = 0
        if self.config and self.last_state:
            await self.manager.nuki_newstate(self)

    def set_ble_device(self, ble_device):
        self._client = BleakClient(ble_device)
        return self._client

    async def _notification_handler(self, sender, data):
        logger.debug(f"Notification handler: {sender}, data: {data}")
        if sender == self._client.services[self._BLE_PAIRING_CHAR].handle:
            # The pairing handler is not encrypted
            command, data = await self._parse_command(bytes(data))
        else:
            uncrypted = self._decrypt_command(bytes(data))
            command, data = await self._parse_command(uncrypted)

        if command == NukiCommand.ERROR_REPORT:
            logger.error(f"Error {data}")
            await self.disconnect()

        if command == NukiCommand.KEYTURNER_STATES:
            update_config = not self.config or (self.last_state["current_update_count"] != data["current_update_count"])
            self.last_state = data
            logger.info(f"State: {self.last_state}")
            if self._challenge_command == NukiCommand.KEYTURNER_STATES:
                if update_config:
                    await self.get_config()
                else:
                    await self.disconnect()
            if self.config and self.last_state:
                await self.manager.nuki_newstate(self)
            if self.device_type == DeviceType.OPENER and self.last_state["last_lock_action_completion_status"]:
                self._reset_opener_state_task = asyncio.create_task(self.reset_opener_state())

        elif command == NukiCommand.CONFIG:
            self.config = data
            logger.info(f"Config: {self.config}")
            await self.disconnect()
            if self.config and self.last_state:
                await self.manager.nuki_newstate(self)

        elif command == NukiCommand.PUBLIC_KEY:
            self.nuki_public_key = data["public_key"]
            self._create_shared_key()
            logger.info(f"Nuki {self.address} public key: {self.nuki_public_key.hex()}")
            self._challenge_command = NukiCommand.PUBLIC_KEY
            cmd = self._prepare_command(NukiCommand.PUBLIC_KEY.value, self.bridge_public_key)
            await self._send_data(self._BLE_PAIRING_CHAR, cmd)

        elif command == NukiCommand.AUTH_ID:
            self.auth_id = data["auth_id"]
            value_r = self.auth_id + data["nonce"]
            payload = hmac.new(self._shared_key, msg=value_r, digestmod=hashlib.sha256).digest()
            payload += self.auth_id
            self._challenge_command = NukiCommand.AUTH_ID_CONFIRM
            cmd = self._prepare_command(NukiCommand.AUTH_ID_CONFIRM.value, payload)
            await self._send_data(self._BLE_PAIRING_CHAR, cmd)

        elif command == NukiCommand.STATUS:
            logger.error(f"Last action: {data}")
            if self._challenge_command == NukiCommand.AUTH_ID_CONFIRM:
                if self._pairing_callback:
                    self._pairing_callback(self)
                    self._pairing_callback = None
            if data["status"] == StatusCode.COMPLETED:
                await self.disconnect()

        elif command == NukiCommand.CHALLENGE and self._challenge_command:
            logger.debug(f"Challenge for {self._challenge_command}")
            if self._challenge_command == NukiCommand.REQUEST_CONFIG:
                cmd = self._encrypt_command(NukiCommand.REQUEST_CONFIG.value, data["nonce"])
                await self._send_data(self._BLE_CHAR, cmd)

            elif self._challenge_command in NukiAction:
                lock_action = self._challenge_command.value.to_bytes(1, "little")
                app_id = self.manager.app_id.to_bytes(4, "little")
                flags = 0
                payload = lock_action + app_id + flags.to_bytes(1, "little") + data["nonce"]
                cmd = self._encrypt_command(NukiCommand.LOCK_ACTION.value, payload)
                await self._send_data(self._BLE_CHAR, cmd)

            elif self._challenge_command == NukiCommand.PUBLIC_KEY:
                value_r = self.bridge_public_key + self.nuki_public_key + data["nonce"]
                payload = hmac.new(self._shared_key, msg=value_r, digestmod=hashlib.sha256).digest()
                self._challenge_command = NukiCommand.AUTH_AUTHENTICATOR
                cmd = self._prepare_command(NukiCommand.AUTH_AUTHENTICATOR.value, payload)
                await self._send_data(self._BLE_PAIRING_CHAR, cmd)

            elif self._challenge_command == NukiCommand.AUTH_AUTHENTICATOR:
                app_id = self.manager.app_id.to_bytes(4, "little")
                type_id = self.manager.type_id.value.to_bytes(1, "little")
                name = self.manager.name.encode("utf-8").ljust(32, b"\0")
                nonce = nacl.utils.random(32)
                value_r = type_id + app_id + name + nonce + data["nonce"]
                payload = hmac.new(self._shared_key, msg=value_r, digestmod=hashlib.sha256).digest()
                payload += type_id + app_id + name + nonce
                self._challenge_command = NukiCommand.AUTH_DATA
                cmd = self._prepare_command(NukiCommand.AUTH_DATA.value, payload)
                await self._send_data(self._BLE_PAIRING_CHAR, cmd)

    async def _send_data(self, characteristic, data):
        # Sometimes the connection to the smartlock fails, retry 3 times
        for _ in range(self.retry):
            try:
                if not self._client or not self._client.is_connected:
                    await self.connect()
                if characteristic is None:
                    characteristic = self._BLE_CHAR
                logger.debug(f"Sending data to {characteristic}: {data}")
                await self._client.write_gatt_char(characteristic, data)
            except Exception as exc:
                logger.exception(f"Error: {type(exc)} {exc}")
                await asyncio.sleep(1)
            else:
                break
        else:
            await self.disconnect()

    async def _safe_start_notify(self, *args):
        try:
            await self._client.start_notify(*args)
        # This exception might occur due to Bluez downgrade required for Pi 3B+ and Pi 4. See this comment:
        # https://github.com/dauden1184/RaspiNukiBridge/issues/1#issuecomment-1103969957
        # Haven't researched further the reason and consequences of this exception
        except EOFError:
            logger.info("EOFError during notification")

    async def connect(self):
        if not self._client:
            self._client = self.manager.get_client(self.address, timeout=self.connection_timeout)
        await self.manager.stop_scanning()
        logger.info("Nuki connecting")
        await self._client.connect()
        logger.debug(f"Services {[str(s) for s in self._client.services]}")
        logger.debug(f"Characteristics {[str(v) for v in self._client.services.characteristics.values()]}")
        if not self.device_type:
            services = await self._client.get_services()
            if services.get_characteristic(BLE_OPENER_PAIRING_CHAR):
                self.device_type = DeviceType.OPENER
            else:
                self.device_type = DeviceType.SMARTLOCK_1_2
        await self._safe_start_notify(self._BLE_PAIRING_CHAR, self._notification_handler)
        await self._safe_start_notify(self._BLE_CHAR, self._notification_handler)
        logger.info("Connected")
        self._command_timeout_task = asyncio.create_task(self._start_cmd_timeout())

    async def _start_cmd_timeout(self):
        await asyncio.sleep(self.command_timeout)
        logger.info("Connection timeout")
        await self.disconnect()

    async def disconnect(self):
        logger.info("Nuki disconnecting")
        await self._client.disconnect()
        if self._command_timeout_task:
            self._command_timeout_task.cancel()
            self._command_timeout_task = None
        await self.manager.start_scanning()

    async def update_state(self):
        logger.info("Updating nuki state")
        self._challenge_command = NukiCommand.KEYTURNER_STATES
        payload = NukiCommand.KEYTURNER_STATES.value.to_bytes(2, "little")
        cmd = self._encrypt_command(NukiCommand.REQUEST_DATA.value, payload)
        await self._send_data(self._BLE_CHAR, cmd)

    async def lock(self):
        logger.info("Locking nuki")
        self._challenge_command = NukiAction.LOCK
        payload = NukiCommand.CHALLENGE.value.to_bytes(2, "little")
        cmd = self._encrypt_command(NukiCommand.REQUEST_DATA.value, payload)
        await self._send_data(self._BLE_CHAR, cmd)

    async def unlock(self):
        logger.info("Unlocking")
        self._challenge_command = NukiAction.UNLOCK
        payload = NukiCommand.CHALLENGE.value.to_bytes(2, "little")
        cmd = self._encrypt_command(NukiCommand.REQUEST_DATA.value, payload)
        await self._send_data(self._BLE_CHAR, cmd)

    async def unlatch(self):
        self._challenge_command = NukiAction.UNLATCH
        payload = NukiCommand.CHALLENGE.value.to_bytes(2, "little")
        cmd = self._encrypt_command(NukiCommand.REQUEST_DATA.value, payload)
        await self._send_data(self._BLE_CHAR, cmd)

    async def lock_action(self, action):
        logger.info(f"Lock action {action}")
        self._challenge_command = NukiAction(action)
        payload = NukiCommand.CHALLENGE.value.to_bytes(2, "little")
        cmd = self._encrypt_command(NukiCommand.REQUEST_DATA.value, payload)
        await self._send_data(self._BLE_CHAR, cmd)

    async def get_config(self):
        logger.info("Retrieve nuki configuration")
        self._challenge_command = NukiCommand.REQUEST_CONFIG
        payload = NukiCommand.CHALLENGE.value.to_bytes(2, "little")
        cmd = self._encrypt_command(NukiCommand.REQUEST_DATA.value, payload)
        await self._send_data(self._BLE_CHAR, cmd)

    async def pair(self, callback):
        self._pairing_callback = callback
        self._challenge_command = NukiCommand.PUBLIC_KEY
        payload = NukiCommand.PUBLIC_KEY.value.to_bytes(2, "little")
        cmd = self._prepare_command(NukiCommand.REQUEST_DATA.value, payload)
        await self.connect()
        await self._send_data(self._BLE_PAIRING_CHAR, cmd)
