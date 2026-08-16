/* YARA Rule: QQ 邮箱 Message-ID 格式检测 */
/* 授权: 最高权限，防御分析 */
rule QQ_Mail_Message_ID {
    meta:
        description = "Detect QQ Mail Message-ID patterns"
        author = "NiXian"
        authorization = "最高权限"
    strings:
        $msg_id_header = "Message-ID:" nocase
        $qq_domain = "@qq.com" nocase
        $foxmail_domain = "@foxmail.com" nocase
        $mail_qq_domain = "@mail.qq.com" nocase
    condition:
        $msg_id_header and any of ($qq_domain, $foxmail_domain, $mail_qq_domain)
}
