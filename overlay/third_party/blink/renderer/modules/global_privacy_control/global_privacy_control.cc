#include "third_party/blink/renderer/modules/global_privacy_control/global_privacy_control.h"

#include "base/feature_list.h"
#include "third_party/blink/public/common/features.h"

namespace blink {

bool GlobalPrivacyControl::globalPrivacyControl(NavigatorBase& navigator) {
  return true;
}

}  // namespace blink
