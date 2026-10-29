import { type ReactElement, type ReactNode } from "react";
import { render, type RenderOptions } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import zhMessages from "../../../../messages/zh.json";

function IntlTestProvider({ children }: { children: ReactNode }) {
    return (
        <NextIntlClientProvider locale="zh" messages={zhMessages} timeZone="Asia/Shanghai">
            {children}
        </NextIntlClientProvider>
    );
}

export function renderWithIntl(ui: ReactElement, options?: Omit<RenderOptions, "wrapper">) {
    return render(ui, { wrapper: IntlTestProvider, ...options });
}
