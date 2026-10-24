"use client";

import { useId, useState } from "react";

import { FailureNotice } from "@/components/app/FailureNotice";
import { SettingsSection } from "@/components/app/SettingsSection";
import { useDirectory } from "@/components/conversations/ConversationDirectory";
import { Field, FieldContent, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Switch } from "@/components/ui/switch";
import { copy } from "@/i18n";
import { switchReachOuts, setVisitOutreachMode } from "@/lib/api";
import { toFailure, type Failure } from "@/lib/failure";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

const t = copy.settings.reachOuts;
const visitOptions = [
  { value: "off", label: "关闭" },
  { value: "natural", label: "自然偶遇" },
  { value: "tonight", label: "今晚想听你先说" },
];
type VisitMode = "off" | "natural" | "tonight";

/** 原有后台搭话与进入聊天时的夜间相遇分别设置，均按账户保存。 */
export function ReachOutsSettings() {
  const { account, accountChanged } = useDirectory();
  const id = useId();
  const [saving, setSaving] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const on = account?.reach_outs ?? null;

  const [savingVisit, setSavingVisit] = useState(false);
  if (!account) return null;
  const visitMode = account.visit_outreach_mode ?? (account.visit_outreach ? "natural" : "off");
  const changeVisit = (next: VisitMode) => {
    setFailure(null);
    setSavingVisit(true);
    accountChanged((current) => ({ ...current, visit_outreach: next !== "off", visit_outreach_mode: next }));
    setVisitOutreachMode(next).catch((error) => {
      accountChanged((current) => ({ ...current, visit_outreach: account.visit_outreach, visit_outreach_mode: account.visit_outreach_mode }));
      setFailure(toFailure(error));
    }).finally(() => setSavingVisit(false));
  };

  const change = (next: boolean) => {
    setFailure(null);
    setSaving(true);
    accountChanged((current) => ({ ...current, reach_outs: next }));
    switchReachOuts(next).then(
      () => setSaving(false),
      (e) => {
        accountChanged((current) => ({ ...current, reach_outs: on }));
        setFailure(toFailure(e));
        setSaving(false);
      },
    );
  };

  return (
    <SettingsSection>
      <FieldGroup>
        {on !== null && <Field orientation="horizontal">
          <FieldContent>
            <FieldLabel htmlFor={`${id}-on`}>{t.on}</FieldLabel>
          </FieldContent>
          <Switch id={`${id}-on`} checked={on} disabled={saving} onCheckedChange={change} />
        </Field>}
        <Field>
          <FieldLabel htmlFor={`${id}-visit`}>夜间相遇</FieldLabel>
          <Select items={visitOptions} value={visitMode} disabled={savingVisit}
            onValueChange={(value) => value && changeVisit(value as VisitMode)}>
            <SelectTrigger id={`${id}-visit`} className="w-full"><SelectValue /></SelectTrigger>
            <SelectContent>
              {visitOptions.map((option) => (
                <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <FieldDescription>
            {visitMode === "off" ? "角色不会在你进入聊天时主动开口。" : visitMode === "natural"
              ? "夜间进入聊天时，角色偶尔会先与你说话。"
              : "今晚进入聊天、稍作停留后，让角色先开口。下一晚恢复「自然偶遇」。"}
          </FieldDescription>
          {visitMode !== "off" && <FieldDescription>
            按个人资料时区，于 22:00 至次日 05:00 生效。请先与角色完成至少一轮对话。
            {visitMode === "natural"
              ? "自然偶遇受关系、近期交流与每晚准备次数限制。"
              : "今晚模式跳过机会抽选、30 分钟近期交流限制与自然模式的每晚准备次数上限。"}
            通话中、角色正在回复或已有未回应的搭话时不会再次开口；启用好感度后，需要达到信任或更高关系。
          </FieldDescription>}
        </Field>
        {failure && <FailureNotice failure={failure} />}
      </FieldGroup>
    </SettingsSection>
  );
}
