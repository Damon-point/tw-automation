"use client";

import { useState } from "react";
import { Button, Confirm, Field, Input, Section, Select, useRunner } from "@/components/admin/kit";
import { Empty, Skeleton, cx } from "@/components/ui";
import { useApi } from "@/lib/api";
import { send, type Account, type AccountsResp } from "@/lib/admin";

const ROLE_TONE: Record<string, string> = {
  admin: "bg-[#fde7da] text-[#b4441b] dark:bg-[#3a2014] dark:text-[#f3a37c]",
  final_reviewer: "bg-ok-soft text-ok",
  mid_reviewer: "bg-info-soft text-info",
  user: "bg-muted-soft text-ink-2",
};

export default function AccountsPage() {
  const { data, loading, reload } = useApi<AccountsResp>("/api/admin/accounts");
  const [name, setName] = useState("");
  const [role, setRole] = useState("user");
  const [reset, setReset] = useState<Account | null>(null);
  const [del, setDel] = useState<Account | null>(null);
  const { busy, run } = useRunner();
  if (loading || !data) return <Skeleton rows={5} />;
  const roles = Object.entries(data.roles).map(([value, label]) => ({ value, label }));
  const valid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(name.trim());

  return (
    <>
      <Section title="새 계정" desc={`아이디는 회사 이메일 주소입니다. 초기 비밀번호 ${data.initial_password} 로 만들어지고, 그 주소로 서비스 주소와 함께 안내 메일이 나가며, 첫 로그인에서 바꾸게 됩니다.`}>
        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_200px_auto] sm:items-end">
          <Field label="아이디 (회사 이메일)"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="hong.gildong@pointmobile.com" /></Field>
          <Field label="역할"><Select value={role} onChange={setRole} options={roles} /></Field>
          <Button busy={busy === "create"} disabled={!valid}
                  onClick={() => run("create", async () => { const r = await send("/api/admin/accounts", { username: name.trim(), role }); setName(""); reload(); return r; })}>
            계정 만들기
          </Button>
        </div>
      </Section>

      <Section title={`계정 ${data.users.length}개`}
               desc={`앱이 보내는 메일은 ${data.system_from_name || "공용 계정"} <${data.system_from || "-"}> 로 나갑니다. 계정의 이메일은 답장 받을 곳과 수신자 목록에 쓰입니다. 계정을 지워도 팀 공용 메일·작업 기록은 내 계정으로 옮겨집니다.`}>
        {data.users.length ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-[13.5px]">
              <thead>
                <tr className="border-b border-line text-left text-[11.5px] font-semibold text-ink-3">
                  <th className="py-2 pr-3">계정</th><th className="px-3 py-2">이메일</th><th className="px-3 py-2">권한</th><th className="py-2 pl-3 text-right">관리</th>
                </tr>
              </thead>
              <tbody>
                {data.users.map((u) => (
                  <tr key={u.id} className="border-b border-line last:border-b-0">
                    <td className="py-2.5 pr-3">
                      <div className="font-semibold">{u.username}{u.me && <span className="ml-1.5 text-[11.5px] font-normal text-ink-3">(나)</span>}</div>
                      {u.must_change && <div className="text-[11.5px] text-warn">첫 로그인 전 · 비밀번호 변경 대기</div>}
                    </td>
                    <td className={cx("px-3 py-2.5", u.email ? "text-ink-2" : "text-err")}>{u.email || "주소를 찾지 못함 — 시스템 명의로 나갑니다"}</td>
                    <td className="px-3 py-2.5">
                      <div className="flex items-center gap-2">
                        <span className={cx("whitespace-nowrap rounded-full px-2 py-0.5 text-[11.5px] font-bold", ROLE_TONE[u.role] ?? ROLE_TONE.user)}>{data.roles[u.role] ?? u.role}</span>
                        <Select value={u.role} options={roles} className="w-[150px] py-1 text-[13px]" disabled={busy === `role${u.id}`}
                                onChange={(v) => run(`role${u.id}`, async () => { const r = await send(`/api/admin/accounts/${u.id}/role`, { role: v }); reload(); return r; })} />
                      </div>
                    </td>
                    <td className="py-2.5 pl-3">
                      <div className="flex justify-end gap-1">
                        <Button variant="ghost" size="sm" onClick={() => setReset(u)}>비밀번호 초기화</Button>
                        <Button variant="ghost" size="sm" disabled={u.me} title={u.me ? "본인 계정은 지울 수 없습니다" : undefined}
                                className="text-err hover:bg-err-soft hover:text-err" onClick={() => setDel(u)}>삭제</Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <Empty>계정 목록은 관리자만 볼 수 있습니다</Empty>}
      </Section>

      <Confirm open={!!reset} onClose={() => setReset(null)} title={`'${reset?.username}' 비밀번호 초기화`} confirmText="초기화"
               onConfirm={async () => { await run("reset", () => send(`/api/admin/accounts/${reset!.id}/reset-password`)); reload(); }}>
        초기 비밀번호로 돌아가고 다음 로그인에서 다시 정하게 됩니다. 아이디(이메일)로 안내 메일이 함께 나갑니다.
      </Confirm>
      <Confirm open={!!del} onClose={() => setDel(null)} title={`'${del?.username}' 계정 삭제`} confirmText="삭제" danger
               onConfirm={async () => { await run("del", () => send(`/api/admin/accounts/${del!.id}`, undefined, "DELETE")); reload(); }}>
        이 계정의 이메일·자격증명은 지워집니다. 팀 공용 메일·작업 기록은 지우지 않고 내 계정으로 옮깁니다.
      </Confirm>
    </>
  );
}
