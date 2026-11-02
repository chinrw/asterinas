# SPDX-License-Identifier: MPL-2.0
"""Check TCP stream coverage against the completed benchmark requests."""
from collections import defaultdict
import struct


def tcp_packets(path, allow_partial=False):
    data=path.read_bytes()
    if len(data)<24:
        if allow_partial:return []
        raise ValueError('missing pcap header')
    formats={b'\xd4\xc3\xb2\xa1':('<',1000), b'\xa1\xb2\xc3\xd4':('>',1000),
             b'\x4d\x3c\xb2\xa1':('<',1), b'\xa1\xb2\x3c\x4d':('>',1)}
    endian,scale=formats[data[:4]]
    linktype=struct.unpack_from(endian+'I',data,20)[0]
    assert linktype==1, f'expected Ethernet capture, got link type {linktype}'
    offset=24
    packets=[]
    while offset<len(data):
        if offset+16>len(data):
            if allow_partial:break
            raise ValueError('incomplete pcap record header')
        sec,sub,caplen,wirelen=struct.unpack_from(endian+'4I',data,offset)
        offset+=16
        if offset+caplen>len(data):
            if allow_partial:break
            raise ValueError('incomplete pcap packet')
        frame=data[offset:offset+caplen];offset+=caplen
        if len(frame)<34 or frame[12:14]!=b'\x08\x00':continue
        ip=14
        ihl=(frame[ip]&15)*4
        if frame[ip]>>4!=4 or frame[ip+9]!=6:continue
        total=struct.unpack_from('!H',frame,ip+2)[0]
        assert not struct.unpack_from('!H',frame,ip+6)[0]&0x3fff, 'fragmented TCP packet'
        tcp=ip+ihl
        assert len(frame)>=tcp+20
        source,destination,seq,ack=struct.unpack_from('!HHII',frame,tcp)
        thl=(frame[tcp+12]>>4)*4
        size=total-ihl-thl
        assert size>=0 and wirelen>=14+total
        packets.append({'realtime_ns':sec*1_000_000_000+sub*scale,
                        'source_port':source,'destination_port':destination,
                        'seq':seq,'ack':ack,'flags':frame[tcp+13], 'payload_bytes':size})
    return packets


def stream_coverage(path, records, server_port, allow_partial=False):
    expected=defaultdict(lambda:[0,0])
    for row in records:
        expected[row['local_port']][0]+=row['request_bytes']
        expected[row['local_port']][1]+=row['response_bytes']
    packets=tcp_packets(path,allow_partial=allow_partial)
    origins={}
    segments=defaultdict(list)
    for packet in packets:
        if packet['destination_port']==server_port and packet['source_port'] in expected:
            key=(packet['source_port'],0)
        elif packet['source_port']==server_port and packet['destination_port'] in expected:
            key=(packet['destination_port'],1)
        else:continue
        if packet['flags']&2:
            origin=(packet['seq']+1)&0xffffffff
            if key in origins:assert origins[key]==origin, 'reused connection tuple'
            origins[key]=origin
        if packet['payload_bytes']:
            segments[key].append((packet['seq'],packet['payload_bytes'],packet['realtime_ns']))
    details=[]
    complete=True
    for port,counts in sorted(expected.items()):
        for direction,want in enumerate(counts):
            key=(port,direction)
            origin=origins.get(key)
            intervals=[]
            before_stream=0
            if origin is not None:
                for seq,size,_ in segments[key]:
                    begin=((seq-origin+(1<<31))&0xffffffff)-(1<<31)
                    finish=begin+size
                    # A probe before the SYN-derived data origin does not carry
                    # bytes delivered to the application stream.
                    if finish<=0:
                        before_stream+=1
                        continue
                    intervals.append((max(0,begin),finish))
                intervals.sort()
            end=0;holes=0
            for begin,finish in intervals:
                if begin>end:holes+=1
                end=max(end,finish)
            ok=origin is not None and holes==0 and end==want
            complete &= ok
            details.append({'local_port':port,'direction':'request' if direction==0 else 'response',
                            'expected_bytes':want,'stream_end':end,'holes':holes,'syn_seen':origin is not None,
                            'pre_stream_segments':before_stream,'complete':ok})
    return {'complete':complete and bool(expected),'streams':details,'packets':len(packets),
            'first_packet_realtime_ns':min((x['realtime_ns'] for x in packets),default=None),
            'last_packet_realtime_ns':max((x['realtime_ns'] for x in packets),default=None)}
