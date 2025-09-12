# 實驗方法

對照組 (HPA)
- testenv (replica = 1 ~ 3)

實驗組 (our method)


實驗時間 也可以 * factor 
load 的 factor 


loading = replica
SLO resource 

定出 SLO 在 1s response time 內


### In radius-tool
```bash
radperf -x -s -f /accounts/account.txt -c 10000 -p 2000 radius-service auth testing123
```

Throughput ~= 220 rps



```shell

root@radius-tool-5585645c4f-nkg58:/# radperf -x -s -f /accounts/account.txt -c 10000 -p 10 radius-service auth testing123
Reading file "/accounts/account.txt" (as RADIUS packets)
Read 1 packets
Total sent        :  10000
Total retransmits :  0
Total succeeded   :  10000
Total failed      :  0
Total no reply    :  0
Total time (s)    :  40.949
Packets/s         :  244
Response times:
< 10 usec  : 0
< 100 usec : 0
< msec     : 0
< 10 msec  : 2
< 0.1s     : 9879
< s        : 119
< 10s      : 0
< 100s     : 0

```


```shell
radtest testuser testpassword 10.121.252.145:31812 0 testing123 localhost localhost
```
