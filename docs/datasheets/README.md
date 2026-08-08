# Datasheets and sources

These vendor reference documents are included for offline bench use. They are
not covered by the repository's software license; see the project `NOTICE`.

The PDFs in this directory were downloaded from Feetech's current product
catalog on 2026-08-05.

| Local file | Manufacturer product page | Manufacturer PDF |
| --- | --- | --- |
| `STS3215-12V-ST-3215-C018.pdf` | [12 V STS3215 C018](https://www.feetechrc.com/525603) | [PDF](https://www.feetechrc.com/Data/feetechrc/upload/file/20260622/6391772519923113075854851.pdf) |
| `STS3250-12V-ST-3250-C001.pdf` | [12 V STS3250 C001](https://www.feetechrc.com/en/562636.html) | [PDF](https://www.feetechrc.com/Data/feetechrc/upload/file/20260622/6391772424007467984928587.pdf) |

SHA-256 checksums:

```text
d0a735a8ddc23585dd7fef40bf7ed87359b6234436103e00bb6e992b5ffe9f39  STS3215-12V-ST-3215-C018.pdf
92926b0571809316d17f0d9cb3d8da8924a7f234f950c7eabc7330be0665c2d7  STS3250-12V-ST-3250-C001.pdf
```

Feetech's current English and Chinese product searches did not return an
STS3095 product page or model-specific PDF. MotorLab therefore supports model
number `2569` through Feetech's shared SMS/STS control table, but deliberately
does not display unverified STS3095 torque, current, or temperature limits.

The register map comes from the SMS/STS table linked by LeRobot to Feetech's
[official e-manual](http://doc.feetech.cn/#/prodinfodownload?srcType=FT-SMS-STS-emanual-229f4476422d4059abfb1cb0).
Feetech's STS3215 technical overview documents operating modes `0–3` and the
shared STS current-feedback unit as
[`1 LSB = 6.5 mA`](https://www.feetechrc.com/20210430-56680.html). MotorLab
keeps the raw count visible while reporting and recording amperes.
