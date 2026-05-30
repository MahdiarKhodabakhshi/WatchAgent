export type WeatherIcon = "sun" | "cloud" | "fog" | "rain" | "storm" | "snow" | "unknown";

export interface WeatherDescriptor {
  label: string;
  category: string;
  icon: WeatherIcon;
}

export function describeWmo(code: number | null): WeatherDescriptor {
  if (code === null) {
    return { label: "Unknown", category: "unknown", icon: "unknown" };
  }

  if (code === 0) {
    return { label: "Clear", category: "clear", icon: "sun" };
  }

  if ([1, 2, 3].includes(code)) {
    return { label: "Cloud cover", category: "cloud", icon: "cloud" };
  }

  if ([45, 48].includes(code)) {
    return { label: "Fog", category: "fog", icon: "fog" };
  }

  if ([51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82].includes(code)) {
    return { label: "Rain", category: "precip", icon: "rain" };
  }

  if ([71, 73, 75, 77, 85, 86].includes(code)) {
    return { label: "Snow", category: "snow", icon: "snow" };
  }

  if ([95, 96, 99].includes(code)) {
    return { label: "Thunderstorm", category: "storm", icon: "storm" };
  }

  return { label: `WMO ${code}`, category: "other", icon: "unknown" };
}
