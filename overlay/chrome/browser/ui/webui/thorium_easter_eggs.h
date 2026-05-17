// Copyright 2026 Alex313031
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THORIUM_EASTER_EGGS_H_
#define THORIUM_EASTER_EGGS_H_

#include "base/memory/ref_counted_memory.h"
#include "chrome/browser/profiles/profile.h"
#include "chrome/common/webui_url_constants.h"
#include "content/public/browser/url_data_source.h"
#include "content/public/browser/web_ui.h"
#include "content/public/browser/web_ui_controller.h"
#include "content/public/browser/webui_config.h"
#include "services/network/public/mojom/content_security_policy.mojom.h"

class ThoriumDataSource : public content::URLDataSource {
 public:
  ThoriumDataSource() {}
  ThoriumDataSource(const ThoriumDataSource&) = delete;
  ThoriumDataSource& operator=(const ThoriumDataSource&) = delete;
  std::string GetSource() override;
  std::string GetMimeType(const GURL& url) override;
  std::string GetContentSecurityPolicy(network::mojom::CSPDirectiveName directive) override;
  void StartDataRequest(const GURL& url,
                        const content::WebContents::Getter& wc_getter,
                        GotDataCallback callback) override;
};

std::string ThoriumDataSource::GetSource() { return "eggs"; }
std::string ThoriumDataSource::GetMimeType(const GURL& url) { return "text/html"; }
std::string ThoriumDataSource::GetContentSecurityPolicy(network::mojom::CSPDirectiveName directive) {
  if (directive == network::mojom::CSPDirectiveName::ScriptSrc)
    return "script-src 'unsafe-inline'";
  return std::string();
}
void ThoriumDataSource::StartDataRequest(const GURL& url,
                      const content::WebContents::Getter& wc_getter,
                      GotDataCallback callback) {
  std::string source = R"(
    <html>
    <head>
      <title>Thorium Easter Eggs</title>
      <meta name="color-scheme" content="light dark">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <link rel="shortcut icon" type="image/x-icon" href="chrome://theme/current-channel-logo@2x">
    <style>
      @import url(chrome://resources/css/text_defaults_md.css);
      body { text-align: center; align-content: center; max-width: 75%; margin: auto;}
      img { max-width: 75%; height: auto; }
    </style>
    </head>
    <body>
      <h2><u>Thorium Easter Eggs WebUI Page</u></h3>
      <hr>
      <p>
        <img src="chrome://theme/IDR_PRODUCT_THORIUM_ELEMENT">
        <hr>
        <img src="chrome://theme/IDR_PRODUCT_THORIUM_ATOMIC">
        <img src="chrome://theme/IDR_PRODUCT_CHROMIUM_QUESTION">
        <img src="chrome://theme/IDR_PRODUCT_THORIUM_GUY">
        <img src="chrome://theme/IDR_PRODUCT_CHROMIUM_BLANK">
        <img src="chrome://theme/IDR_PRODUCT_AI_CHROME">
      </p>
    </body>
    </html>
  )";
  std::move(callback).Run(base::MakeRefCounted<base::RefCountedString>(std::move(source)));
}

class ThoriumWebUILoad;
class ThoriumWebUILoadUIConfig : public content::DefaultWebUIConfig<ThoriumWebUILoad> {
  public:
   ThoriumWebUILoadUIConfig() : DefaultWebUIConfig("chrome", chrome::kChromeUIEggsHost) {}
};

class ThoriumWebUILoad : public content::WebUIController {
 public:
  ThoriumWebUILoad(content::WebUI* web_ui) : content::WebUIController(web_ui) {
    content::URLDataSource::Add(Profile::FromWebUI(web_ui), std::make_unique<ThoriumDataSource>());
  }
  ThoriumWebUILoad(const ThoriumWebUILoad&) = delete;
  ThoriumWebUILoad& operator=(const ThoriumWebUILoad&) = delete;
};

#endif  // THORIUM_EASTER_EGGS_H_
