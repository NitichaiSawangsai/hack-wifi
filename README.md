Kali สแกน Wi-Fi ใหม่:
```
$ nmcli device wifi rescan
$ nmcli -f IN-USE,SSID,SIGNAL,SECURITY device wifi list
$ nmcli device wifi connect "wifi_2.4G" password "รหัสผ่าน" 
$ nmcli connection show
$ nmcli device disconnect wlan0 or $ nmcli connection delete "wifi_2.4G"
$ nmcli -t -f GENERAL.STATE device show wlan0 | grep -q '100 (connected)' && echo Y || echo N
```

Run
```
$ python3 wifi-audit.py
```

ไฟล์ที่ต้องมีได้แก่

password.txt
  ตัวอย่างในไฟล์
```
ExamplePassword123
CompanyTest2026
ExampleStrongPassword2026
```

wifi.txt
  ตัวอย่างในไฟล์
```
Office-Staff
Office-Guest
Office-IoT
```
