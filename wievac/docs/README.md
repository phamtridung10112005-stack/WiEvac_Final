# WiEvac Docs Index

## Nguon quy uoc

1. `../AGENTS.md` - luat bat buoc cho Codex va implementation.
2. `edge-result-v5-contract.md` - kien truc dich va goi EdgeResult local.
3. `project-conventions.md` - naming, ownership, data va versioning.
4. `flex-threshold-policy.md` - Formula Flex, score, baseline va threshold.
5. `data-ai-lifecycle.md` - dataset, RX tiny AI va Pi trend model.
6. `verification-checklist.md` - cong xac nhan truoc khi goi active.
7. `engineering-change-protocol.md` va `experiment-ledger.md` - quy trinh
   thay doi, evidence va rollback.

## Pham vi hien tai

- Chi EdgeResult compact schema 6 va hai link trong `config/corridors/` la
  cau hinh active.
- Archive cu khong duoc dua vao build active.
- OTA khong thuoc pham vi firmware hoac Pi hien tai.

## Quy tac doc trang thai

Tai lieu co the mo ta thiet ke truoc khi code xong. Luon tim dong `Status` va
phan biet:

```text
DESIGN_ONLY -> IMPLEMENTED -> SHADOW -> ACTIVE -> FIELD_VERIFIED
```

Khong dung tai lieu thiet ke hoac build software de tuyen bo do chinh xac
thuc dia, calibration confidence hay kha nang hoat dong moi corridor.
