// @vitest-environment happy-dom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import GlobalSidebar from "@/components/layout/GlobalSidebar";
import BottomTabBar from "@/components/layout/BottomTabBar";
import messages from "../../messages/en.json";

afterEach(() => { cleanup(); window.location.hash = "#/"; });

describe("Atelier product navigation", () => {
    for (const [name, Navigation] of [["desktop", GlobalSidebar], ["mobile", BottomTabBar]] as const) {
        it(`${name} exposes Atelier and navigates without changing other products`, () => {
            const onTabChange = vi.fn();
            render(<NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
                <Navigation activeTab="workspace" onTabChange={onTabChange} />
            </NextIntlClientProvider>);

            fireEvent.click(screen.getByRole("button", { name: "Atelier" }));
            expect(onTabChange).toHaveBeenLastCalledWith("atelier");
            expect(window.location.hash).toBe("#/atelier");
            fireEvent.click(screen.getByRole("button", { name: messages.nav.playground }));
            expect(onTabChange).toHaveBeenLastCalledWith("playground");
            expect(window.location.hash).toBe("#/playground");
            expect(screen.getAllByRole("button", { name: messages.nav.settings })).toHaveLength(1);
        });
    }
});
