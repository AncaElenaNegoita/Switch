#!/usr/bin/python3
import sys
import struct
import wrapper
import threading
import time
from wrapper import recv_from_any_link, send_to_link, get_switch_mac, get_interface_name
import struct

# Struct that stores information about the switch in STP.
class BPDUConfig:
	def __init__(self, root_bridge_id, root_path_cost, bridge_id):
		self.root_bridge_id = root_bridge_id
		self.root_path_cost = root_path_cost
		self.bridge_id = bridge_id

def parse_ethernet_header(data):
	# Unpack the header fields from the byte array
	#dest_mac, src_mac, ethertype = struct.unpack('!6s6sH', data[:14])
	dest_mac = data[0:6]
	src_mac = data[6:12]
	
	# Extract ethertype. Under 802.1Q, this may be the bytes from the VLAN TAG
	ether_type = (data[12] << 8) + data[13]

	vlan_id = -1
	# Check for VLAN tag (0x8100 in network byte order is b'\x81\x00')
	if ether_type == 0x8200:
		vlan_tci = int.from_bytes(data[14:16], byteorder='big')
		vlan_id = vlan_tci & 0x0FFF  # extract the 12-bit VLAN ID
		ether_type = (data[16] << 8) + data[17]

	return dest_mac, src_mac, ether_type, vlan_id

def create_vlan_tag(vlan_id):
	# 0x8100 for the Ethertype for 802.1Q
	# vlan_id & 0x0FFF ensures that only the last 12 bits are used
	return struct.pack('!H', 0x8200) + struct.pack('!H', vlan_id & 0x0FFF)

switch_info = None

# 0 BLOCKING, 1 LISTENING
def send_bdpu_every_sec(interfaces, port_access):
	global switch_info
	print("sunt in functie")
	print(switch_info.root_bridge_id)
	while True:
		# TODO Send BDPU every second if necessary
		# If the current switch is the root bridge, send packets to all other switches
		if switch_info.bridge_id == switch_info.root_bridge_id:
			for i in interfaces:
				if get_interface_name(i) in port_access:
					data = bytes([0x01, 0x80, 0xc2, 0x00, 0x00, 0x00]) + \
							switch_info.root_bridge_id + switch_info.root_path_cost + \
							switch_info.bridge_id
					send_to_link(i, data, 26)
		time.sleep(1)

def main():
	global switch_info
	# The table for the switch in which the mac address and its associated port are stored
	mac_table = {}
	# The switch configuration in which the port is associated with its vlan (or is type
	# trunk)
	switch_config = {}
	# Set the port with 0 for blocking and 1 for listening
	port_access = {}
	# init returns the max interface number. Our interfaces
	# are 0, 1, 2, ..., init_ret value + 1
	switch_id = sys.argv[1]
	# The filename for each switch configuration
	filename =f'configs/switch{switch_id}.cfg'
	# The priority of the current switch
	switch_prop = None
	
	with open(filename, 'r') as file:
		switch_prop = int(file.readline().strip())
		for line in file:
			# Split the line at the first space
			line_read = line.split(None, 1)
			# Strip it at space
			sconfig = [elem.strip() for elem in line_read]
			switch_config[sconfig[0]] = sconfig[1]
			# If the port is connected to another switch (trunk), set the port to LISTENING
			# (can receive data through that connection)
			if sconfig[1].isalpha():
				port_access[sconfig[0]] = 1
	# The initial cost of the path to the root bridge is 0 (the current switch thinks it is the root)
	s = 0
	# Configure the switch informations
	switch_info = BPDUConfig(switch_prop.to_bytes(8, byteorder='big'), s.to_bytes(4, byteorder='big'), switch_prop.to_bytes(8, byteorder='big'))

	num_interfaces = wrapper.init(sys.argv[2:])
	interfaces = range(0, num_interfaces)

	print("# Starting switch with id {}".format(switch_id), flush=True)
	print("[INFO] Switch MAC", ':'.join(f'{b:02x}' for b in get_switch_mac()))

	# Create and start a new thread that deals with sending BDPU
	t = threading.Thread(target=send_bdpu_every_sec, args=(interfaces, port_access))
	t.start()

	# Printing interface names
	for i in interfaces:
		print(get_interface_name(i))

	while True:
		# Note that data is of type bytes([...]).
		# b1 = bytes([72, 101, 108, 108, 111])  # "Hello"
		# b2 = bytes([32, 87, 111, 114, 108, 100])  # " World"
		# b3 = b1[0:2] + b[3:4].
		interface, data, length = recv_from_any_link()
		# The received packet is from STP
		if data[0:6] == bytes([0x01, 0x80, 0xc2, 0x00, 0x00, 0x00]):
			# Get the configuration information of the sender switch
			other_switch_info = BPDUConfig(data[6:14], data[14:18], data[18:26])
			# Check if the sender switch has a better route bridge (lower priority than what
			# it already had stored)
			if other_switch_info.root_bridge_id <= switch_info.root_bridge_id:
				before_id = switch_info.root_bridge_id
				switch_info.root_bridge_id = other_switch_info.root_bridge_id
				switch_info.root_path_cost = bytes(int.from_bytes(other_switch_info.root_path_cost, \
													byteorder='big') + 10)
				# If before the modification of the root bridge the current switch thought
				# it was the root, switch all ports to BLOCKING without the one the packet
				# came from
				if before_id == switch_info.bridge_id:
					for i in interfaces:
						if get_interface_name(i) in port_access:
							if i == interface:
								port_access[get_interface_name(i)] = 1 # set to LISTENING
							else:
								port_access[get_interface_name(i)] = 0 # set to BLOCKING
								data = data[0:6] + switch_info.root_bridge_id + \
										switch_info.root_path_cost + switch_info.bridge_id
								send_to_link(i, data, length)
			# The 2 switches have the same root bridge stored
			elif other_switch_info.root_bridge_id == switch_info.root_bridge_id:
				# The port is listening and there is a shorter path found from root bridge
				cost = bytes(int.from_bytes(other_switch_info.root_path_cost, byteorder='big') + 10)
				if port[get_interface_name(interface)] == 1 and cost < switch_info.root_path_cost:
					switch_info.root_path_cost = cost
				# The port is blocked
				else:
					# If the path is better from the current switch, switch the port to listening
					if other_switch_info.root_path_cost > switch_info.root_path_cost:
						port[get_interface_name(interface)] = 1
			# If the packet came back to the current switch, a loop was found, adn the port is
			# set to BLOCKING
			elif other_switch_info.bridge_id == switch_info.bridge_id:
				port_access[get_interface_name(interface)] = 0

			#  If in the end the switch remained the root bridge, put all the ports to listening			
			if switch_info.bridge_id == switch_info.root_bridge_id:
				for i in interfaces:
					if get_interface_name(i) in port_access:
						port_access[get_interface_name(i)] = 1 # set to LISTENING
		else:
			dest_mac, src_mac, ethertype, vlan_id = parse_ethernet_header(data)

			# Print the MAC src and MAC dst in human readable format
			dest_mac = ':'.join(f'{b:02x}' for b in dest_mac)
			src_mac = ':'.join(f'{b:02x}' for b in src_mac)

			# Note. Adding a VLAN tag can be as easy as
			# tagged_frame = data[0:12] + create_vlan_tag(10) + data[12:]

			print(f'Destination MAC: {dest_mac}')
			print(f'Source MAC: {src_mac}')
			print(f'EtherType: {ethertype}')

			print("Received frame of size {} on interface {}".format(length, interface), flush=True)

			# TODO: Implement VLAN support
			# TODO: Implement forwarding with learning

			# Verify if the mac source is already in its CAM table. If not, add it
			if src_mac not in mac_table:
				mac_table[src_mac] = interface
				
			# Received a packet from a host
			if vlan_id == -1:
				# Create the header where the tag and VLAN are stored
				vlan_id = int(switch_config[get_interface_name(interface)])
				vlan_tci = (0x8200 << 16) | (vlan_id & 0x0FFF)
				# Convert vlan_header to bytes
				vlan_header = vlan_tci.to_bytes(4, byteorder='big')
				length += 4

				# Insert the 4-byte VLAN header into the data bytearray
				data = data[:12] + vlan_header + data[12:]
			# If the MAC is already in the table, the route is known (to which port the
			# information is sent)
			if dest_mac in mac_table:
				port = get_interface_name(mac_table[dest_mac])
				# If it's sent to a host that has the same VLAN as the sender, eliminate the
				# tag and send the initial packet
				if switch_config[port].isdigit():
					if int(switch_config[port]) == vlan_id:
						length_new = length - 4
						data_new = data[:12] + data[16:]
						send_to_link(mac_table[dest_mac], data_new, length_new)
				# If it's sent to a switch, if the connection is open, send forward
				elif switch_config[port].isalpha():
					if port_access[port] == 1:
						send_to_link(mac_table[dest_mac], data, length)
			else:
				# The route isn't known, so the network system needs to be flooded in order
				# to find the destination
				for i in interfaces:
					# The packet won't be sent back from where it came
					if i != interface:
						port = get_interface_name(i)
						# If it's sent to a host that has the same VLAN as the sender, eliminate the
						# tag and send the initial packet
						if switch_config[port].isdigit():
							if int(switch_config[port]) == vlan_id:
								length_new = length - 4
								data_new = data[:12] + data[16:]
								send_to_link(i, data_new, length_new)
						# If it's sent to a switch, if the connection is open, send forward
						elif switch_config[port].isalpha():
							if port_access[port] == 1:
								send_to_link(i, data, length)

		# TODO: Implement STP support

		# data is of type bytes.
		# send_to_link(i, data, length)

if __name__ == "__main__":
	main()
