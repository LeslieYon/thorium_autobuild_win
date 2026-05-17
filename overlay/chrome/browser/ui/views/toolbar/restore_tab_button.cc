// Copyright 2026 The Chromium Authors and Alex313031
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "chrome/browser/ui/views/toolbar/restore_tab_button.h"

#include "base/command_line.h"
#include "base/logging.h"
#include "base/strings/string_number_conversions.h"
#include "chrome/app/chrome_command_ids.h"
#include "chrome/browser/command_updater.h"
#include "chrome/browser/external_protocol/external_protocol_handler.h"
#include "chrome/browser/ui/browser.h"
#include "chrome/browser/ui/view_ids.h"
#include "chrome/grit/generated_resources.h"
#include "components/vector_icons/vector_icons.h"
#include "ui/base/l10n/l10n_util.h"
#include "ui/base/metadata/metadata_impl_macros.h"
#include "ui/base/ui_base_features.h"
#include "ui/views/accessibility/view_accessibility.h"
#include "ui/views/controls/button/button_controller.h"

RestoreTabButton::RestoreTabButton(CommandUpdater* command_updater)
    : ToolbarButton(base::BindRepeating(&RestoreTabButton::ButtonPressed,
                                        base::Unretained(this))),
      command_updater_(command_updater) {

  SetIcon();

  SetTooltipText(l10n_util::GetStringUTF16(IDS_RESTORE_TAB_BUTTON_TOOLTIP));
  SetAccessibleName(l10n_util::GetStringUTF16(IDS_RESTORE_TAB_BUTTON_NAME));
  button_controller()->set_notify_action(
      views::ButtonController::NotifyAction::kOnPress);
  SetID(VIEW_ID_RESTORE_TAB_BUTTON);
  SizeToPreferredSize();
}

RestoreTabButton::~RestoreTabButton() = default;

void RestoreTabButton::ButtonPressed() {
  ExternalProtocolHandler::PermitLaunchUrl();

  int command;
  // See chrome/app/chrome_command_ids.h for all possible commands
  if (base::CommandLine::ForCurrentProcess()->HasSwitch("button-command")) {
    const std::string button_command =
        base::CommandLine::ForCurrentProcess()->GetSwitchValueASCII("button-command");
    command = base::StringToInt(button_command, &command);
    LOG(ERROR) << command;
  } else {
    command = IDC_RESTORE_TAB;
  }
  const int command_to_exec = command;

  ExecuteBrowserCommand(command_to_exec);
}

void RestoreTabButton::SetIcon() {
  const gfx::VectorIcon& restore_icon =
      vector_icons::kRestoreTabIcon;
  SetVectorIcons(restore_icon, restore_icon);
}

void RestoreTabButton::ExecuteBrowserCommand(int command) {
  if (!command_updater_) {
    return;
  }
  command_updater_->ExecuteCommand(command);
}

BEGIN_METADATA(RestoreTabButton)
END_METADATA
