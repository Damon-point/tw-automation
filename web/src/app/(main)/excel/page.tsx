import { redirect } from "next/navigation";

/** 예전 주소 — FOTA DB 열람은 왼쪽 메뉴의 /fota-db 로 옮겼다(2026-10-20). */
export default function OldExcel() {
  redirect("/fota-db");
}
