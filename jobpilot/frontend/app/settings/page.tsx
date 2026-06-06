"use client";

import * as React from "react";
import useSWR from "swr";
import {
  BriefcaseBusiness,
  GraduationCap,
  Minus,
  Plus,
  Save,
  ShieldCheck,
  Sparkles,
  Target,
  UserRound,
} from "lucide-react";

import { PageHeader } from "@/components/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Select } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  api,
  type ApplicationPreferences,
  type EEOData,
  type ExperienceItem,
  type ProfileInput,
  type ProjectItem,
} from "@/lib/api";
import { splitCsv } from "@/lib/format";

const emptyPreferences: ApplicationPreferences = {
  remote_preference: "",
  employment_types: ["Full-time"],
  earliest_start_date: "",
  notice_period: "",
  preferred_timezone: "",
  requires_sponsorship: null,
  sponsorship_notes: "",
  open_to_background_check: null,
  open_to_drug_test: null,
};

const emptyEeo: EEOData = {
  gender: "",
  race_ethnicity: "",
  veteran_status: "",
  disability_status: "",
};

const emptyProfile: ProfileInput = {
  full_name: "",
  first_name: "",
  last_name: "",
  email: "",
  phone: "",
  location: "",
  address_street: "",
  address_city: "",
  address_state: "",
  address_zip: "",
  address_country: "",
  linkedin_url: "",
  github_url: "",
  portfolio_url: "",
  work_authorization: null,
  years_experience: 0,
  willing_to_relocate: false,
  salary_min: null,
  salary_max: null,
  summary: "",
  skills_json: [],
  education_json: [],
  target_roles_json: [],
  preferred_locations_json: [],
  experience_json: [],
  projects_json: [],
  achievements_json: [],
  certifications_json: [],
  languages_json: [],
  application_preferences_json: emptyPreferences,
  eeo_json: emptyEeo,
};

const emptyExperience: ExperienceItem = {
  company: "",
  title: "",
  location: "",
  start_date: "",
  end_date: "",
  highlights: [],
  technologies: [],
};

const emptyProject: ProjectItem = {
  name: "",
  url: "",
  description: "",
  impact: "",
  technologies: [],
};

export default function SettingsPage() {
  const { data: profile, mutate } = useSWR("settings-profile", api.getProfile, {
    shouldRetryOnError: false,
  });
  const [message, setMessage] = React.useState<string | null>(null);
  const [profileForm, setProfileForm] = React.useState<ProfileInput>(emptyProfile);
  const [skillsText, setSkillsText] = React.useState("");
  const [targetRolesText, setTargetRolesText] = React.useState("");
  const [preferredLocationsText, setPreferredLocationsText] = React.useState("");
  const [achievementsText, setAchievementsText] = React.useState("");
  const [certificationsText, setCertificationsText] = React.useState("");
  const [languagesText, setLanguagesText] = React.useState("");
  const [scrapeInterval, setScrapeInterval] = React.useState(6);
  const [dailyLimit, setDailyLimit] = React.useState(20);
  const [queries, setQueries] = React.useState(
    "AI Engineer, Machine Learning Engineer, Software Engineer, Software Development Engineer",
  );
  const [blacklist, setBlacklist] = React.useState("");

  React.useEffect(() => {
    if (!profile) return;
    const nextProfile: ProfileInput = {
      full_name: profile.full_name,
      first_name: profile.first_name ?? "",
      last_name: profile.last_name ?? "",
      email: profile.email,
      phone: profile.phone ?? "",
      location: profile.location ?? "",
      address_street: profile.address_street ?? "",
      address_city: profile.address_city ?? "",
      address_state: profile.address_state ?? "",
      address_zip: profile.address_zip ?? "",
      address_country: profile.address_country ?? "",
      linkedin_url: profile.linkedin_url ?? "",
      github_url: profile.github_url ?? "",
      portfolio_url: profile.portfolio_url ?? "",
      work_authorization: profile.work_authorization ?? null,
      years_experience: profile.years_experience ?? 0,
      willing_to_relocate: profile.willing_to_relocate ?? false,
      salary_min: profile.salary_min ?? null,
      salary_max: profile.salary_max ?? null,
      summary: profile.summary ?? "",
      skills_json: profile.skills_json ?? [],
      education_json: profile.education_json ?? [],
      target_roles_json: profile.target_roles_json ?? [],
      preferred_locations_json: profile.preferred_locations_json ?? [],
      experience_json: profile.experience_json ?? [],
      projects_json: profile.projects_json ?? [],
      achievements_json: profile.achievements_json ?? [],
      certifications_json: profile.certifications_json ?? [],
      languages_json: profile.languages_json ?? [],
      application_preferences_json: {
        ...emptyPreferences,
        ...(profile.application_preferences_json ?? {}),
      },
      eeo_json: {
        ...emptyEeo,
        ...(profile.eeo_json ?? {}),
      },
    };
    setProfileForm(nextProfile);
    setSkillsText(nextProfile.skills_json.join(", "));
    setTargetRolesText(nextProfile.target_roles_json.join(", "));
    setPreferredLocationsText(nextProfile.preferred_locations_json.join(", "));
    setAchievementsText(nextProfile.achievements_json.join("\n"));
    setCertificationsText(nextProfile.certifications_json.join(", "));
    setLanguagesText(nextProfile.languages_json.join(", "));
  }, [profile]);

  function patchProfile(update: Partial<ProfileInput>) {
    setProfileForm((current) => ({ ...current, ...update }));
  }

  function patchPreferences(update: Partial<ApplicationPreferences>) {
    setProfileForm((current) => ({
      ...current,
      application_preferences_json: {
        ...current.application_preferences_json,
        ...update,
      },
    }));
  }

  function patchEeo(update: Partial<EEOData>) {
    setProfileForm((current) => ({
      ...current,
      eeo_json: {
        ...current.eeo_json,
        ...update,
      },
    }));
  }

  async function saveProfile() {
    setMessage(null);
    try {
      const payload = buildProfilePayload({
        form: profileForm,
        skillsText,
        targetRolesText,
        preferredLocationsText,
        achievementsText,
        certificationsText,
        languagesText,
      });
      if (profile?.id) await api.updateProfile(payload);
      else await api.createProfile(payload);
      await mutate();
      setMessage("Profile saved.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not save profile");
    }
  }

  async function seedQaMemory() {
    setMessage(null);
    try {
      const result = await api.setupProfileComplete();
      setMessage(`Seeded ${result.qa_memory_seeded} Q&A entries.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not seed Q&A memory");
    }
  }

  const completion = profileCompletion(profileForm, skillsText, targetRolesText);

  return (
    <>
      <PageHeader title="Settings" description="Profile, credentials, scraper limits, and notifications." />

      {message && (
        <div className="mb-4 rounded-md border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">
          {message}
        </div>
      )}

      <Tabs defaultValue="profile">
        <TabsList>
          <TabsTrigger value="profile">Profile</TabsTrigger>
          <TabsTrigger value="keys">API Keys</TabsTrigger>
          <TabsTrigger value="scraper">Scraper</TabsTrigger>
          <TabsTrigger value="notifications">Notifications</TabsTrigger>
        </TabsList>

        <TabsContent value="profile">
          <div className="grid gap-5">
            <Card>
              <CardHeader>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <CardTitle>Profile Setup</CardTitle>
                    <CardDescription>Used by resume scoring, cover letters, outreach, Q&A, and form filling.</CardDescription>
                  </div>
                  <Badge variant={completion >= 75 ? "default" : "secondary"}>{completion}% complete</Badge>
                </div>
              </CardHeader>
              <CardContent className="grid gap-3">
                <Progress value={completion} />
                <div className="flex flex-wrap gap-2">
                  <Button type="button" onClick={() => void saveProfile()}>
                    <Save className="h-4 w-4" aria-hidden="true" />
                    Save Profile
                  </Button>
                  <Button type="button" variant="outline" onClick={() => void seedQaMemory()}>
                    <ShieldCheck className="h-4 w-4" aria-hidden="true" />
                    Setup Complete
                  </Button>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <UserRound className="h-5 w-5" aria-hidden="true" />
                  Identity
                </CardTitle>
                <CardDescription>Contact details and public links used in forms and outreach.</CardDescription>
              </CardHeader>
              <CardContent className="grid gap-4">
                <div className="grid gap-4 md:grid-cols-2">
                  <Field label="First name">
                    <Input
                      value={profileForm.first_name ?? ""}
                      onChange={(event) => patchProfile({
                        first_name: event.target.value,
                        full_name: `${event.target.value} ${profileForm.last_name ?? ""}`.trim(),
                      })}
                    />
                  </Field>
                  <Field label="Last name">
                    <Input
                      value={profileForm.last_name ?? ""}
                      onChange={(event) => patchProfile({
                        last_name: event.target.value,
                        full_name: `${profileForm.first_name ?? ""} ${event.target.value}`.trim(),
                      })}
                    />
                  </Field>
                  <Field label="Email">
                    <Input
                      type="email"
                      value={profileForm.email}
                      onChange={(event) => patchProfile({ email: event.target.value })}
                    />
                  </Field>
                  <Field label="Phone">
                    <Input
                      value={profileForm.phone ?? ""}
                      onChange={(event) => patchProfile({ phone: event.target.value })}
                    />
                  </Field>
                </div>

                <p className="text-sm font-medium text-slate-700 mt-2">Address</p>
                <div className="grid gap-4 md:grid-cols-2">
                  <Field label="Street address" className="col-span-2">
                    <Input
                      value={profileForm.address_street ?? ""}
                      onChange={(event) => patchProfile({ address_street: event.target.value })}
                      placeholder="123 Main St"
                    />
                  </Field>
                  <Field label="City">
                    <Input
                      value={profileForm.address_city ?? ""}
                      onChange={(event) => patchProfile({
                        address_city: event.target.value,
                        location: `${event.target.value}, ${profileForm.address_state ?? ""}`.trim().replace(/,\s*$/, ""),
                      })}
                      placeholder="Tempe"
                    />
                  </Field>
                  <Field label="State / Province">
                    <Input
                      value={profileForm.address_state ?? ""}
                      onChange={(event) => patchProfile({
                        address_state: event.target.value,
                        location: `${profileForm.address_city ?? ""}, ${event.target.value}`.trim().replace(/^,\s*/, ""),
                      })}
                      placeholder="AZ"
                    />
                  </Field>
                  <Field label="ZIP / Postal code">
                    <Input
                      value={profileForm.address_zip ?? ""}
                      onChange={(event) => patchProfile({ address_zip: event.target.value })}
                      placeholder="85281"
                    />
                  </Field>
                  <Field label="Country">
                    <Input
                      value={profileForm.address_country ?? ""}
                      onChange={(event) => patchProfile({ address_country: event.target.value })}
                      placeholder="United States"
                    />
                  </Field>
                </div>
                <div className="grid gap-4 md:grid-cols-2">
                  <Field label="Location (derived)">
                    <Input
                      value={profileForm.location ?? ""}
                      onChange={(event) => patchProfile({ location: event.target.value })}
                      placeholder="auto-filled from City + State"
                    />
                  </Field>
                  <Field label="LinkedIn URL">
                    <Input
                      value={profileForm.linkedin_url ?? ""}
                      onChange={(event) => patchProfile({ linkedin_url: event.target.value })}
                    />
                  </Field>
                  <Field label="GitHub URL">
                    <Input
                      value={profileForm.github_url ?? ""}
                      onChange={(event) => patchProfile({ github_url: event.target.value })}
                    />
                  </Field>
                  <Field label="Portfolio URL">
                    <Input
                      value={profileForm.portfolio_url ?? ""}
                      onChange={(event) => patchProfile({ portfolio_url: event.target.value })}
                    />
                  </Field>
                  <Field label="Years of experience">
                    <Input
                      type="number"
                      min={0}
                      value={profileForm.years_experience}
                      onChange={(event) => patchProfile({ years_experience: Number(event.target.value) })}
                    />
                  </Field>
                </div>
                <Field label="Professional summary">
                  <Textarea
                    rows={5}
                    value={profileForm.summary ?? ""}
                    onChange={(event) => patchProfile({ summary: event.target.value })}
                  />
                </Field>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Target className="h-5 w-5" aria-hidden="true" />
                  Targets And Preferences
                </CardTitle>
                <CardDescription>Controls application answers, filtering, and cover letter positioning.</CardDescription>
              </CardHeader>
              <CardContent className="grid gap-4">
                <div className="grid gap-4 md:grid-cols-2">
                  <Field label="Target roles">
                    <Input
                      value={targetRolesText}
                      onChange={(event) => setTargetRolesText(event.target.value)}
                      placeholder="AI Engineer, Backend Engineer, SDE"
                    />
                  </Field>
                  <Field label="Preferred locations">
                    <Input
                      value={preferredLocationsText}
                      onChange={(event) => setPreferredLocationsText(event.target.value)}
                      placeholder="Remote, Phoenix, Seattle, New York"
                    />
                  </Field>
                  <Field label="Work authorization">
                    <Select
                      value={profileForm.work_authorization ?? ""}
                      onChange={(event) =>
                        patchProfile({
                          work_authorization: (event.target.value || null) as ProfileInput["work_authorization"],
                        })
                      }
                    >
                      <option value="">Select status</option>
                      <option value="US Citizen">US Citizen</option>
                      <option value="GC">Green Card</option>
                      <option value="H1B">H1B</option>
                      <option value="OPT">OPT</option>
                      <option value="CPT">CPT</option>
                    </Select>
                  </Field>
                  <Field label="Remote preference">
                    <Select
                      value={profileForm.application_preferences_json.remote_preference ?? ""}
                      onChange={(event) => patchPreferences({ remote_preference: event.target.value })}
                    >
                      <option value="">No preference set</option>
                      <option value="Remote preferred">Remote preferred</option>
                      <option value="Hybrid preferred">Hybrid preferred</option>
                      <option value="On-site preferred">On-site preferred</option>
                      <option value="Open to remote, hybrid, or on-site">Open to any</option>
                    </Select>
                  </Field>
                  <Field label="Salary min">
                    <Input
                      type="number"
                      value={profileForm.salary_min ?? ""}
                      onChange={(event) =>
                        patchProfile({ salary_min: event.target.value ? Number(event.target.value) : null })
                      }
                    />
                  </Field>
                  <Field label="Salary max">
                    <Input
                      type="number"
                      value={profileForm.salary_max ?? ""}
                      onChange={(event) =>
                        patchProfile({ salary_max: event.target.value ? Number(event.target.value) : null })
                      }
                    />
                  </Field>
                  <Field label="Earliest start date">
                    <Input
                      type="date"
                      value={profileForm.application_preferences_json.earliest_start_date ?? ""}
                      onChange={(event) => patchPreferences({ earliest_start_date: event.target.value })}
                    />
                  </Field>
                  <Field label="Notice period">
                    <Input
                      value={profileForm.application_preferences_json.notice_period ?? ""}
                      onChange={(event) => patchPreferences({ notice_period: event.target.value })}
                      placeholder="Immediately, 2 weeks, after OPT start"
                    />
                  </Field>
                  <Field label="Preferred timezone">
                    <Input
                      value={profileForm.application_preferences_json.preferred_timezone ?? ""}
                      onChange={(event) => patchPreferences({ preferred_timezone: event.target.value })}
                      placeholder="America/Phoenix, PT, ET"
                    />
                  </Field>
                  <Field label="Requires sponsorship">
                    <Select
                      value={boolToSelect(profileForm.application_preferences_json.requires_sponsorship)}
                      onChange={(event) => patchPreferences({ requires_sponsorship: selectToBool(event.target.value) })}
                    >
                      <option value="">Not set</option>
                      <option value="false">No</option>
                      <option value="true">Yes</option>
                    </Select>
                  </Field>
                </div>
                <div className="grid gap-3 md:grid-cols-3">
                  {["Full-time", "Contract", "Internship"].map((type) => (
                    <CheckboxRow
                      key={type}
                      label={type}
                      checked={profileForm.application_preferences_json.employment_types.includes(type)}
                      onChange={(checked) => {
                        const current = profileForm.application_preferences_json.employment_types;
                        patchPreferences({
                          employment_types: checked ? [...current, type] : current.filter((item) => item !== type),
                        });
                      }}
                    />
                  ))}
                  <CheckboxRow
                    label="Willing to relocate"
                    checked={profileForm.willing_to_relocate}
                    onChange={(checked) => patchProfile({ willing_to_relocate: checked })}
                  />
                  <CheckboxRow
                    label="Open to background check"
                    checked={profileForm.application_preferences_json.open_to_background_check === true}
                    onChange={(checked) => patchPreferences({ open_to_background_check: checked })}
                  />
                  <CheckboxRow
                    label="Open to drug test"
                    checked={profileForm.application_preferences_json.open_to_drug_test === true}
                    onChange={(checked) => patchPreferences({ open_to_drug_test: checked })}
                  />
                </div>
                <Field label="Sponsorship notes">
                  <Textarea
                    rows={3}
                    value={profileForm.application_preferences_json.sponsorship_notes ?? ""}
                    onChange={(event) => patchPreferences({ sponsorship_notes: event.target.value })}
                    placeholder="Optional wording for visa or work authorization questions."
                  />
                </Field>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Sparkles className="h-5 w-5" aria-hidden="true" />
                  Skills And Signals
                </CardTitle>
                <CardDescription>Keywords and proof points used by ATS scoring and generated drafts.</CardDescription>
              </CardHeader>
              <CardContent className="grid gap-4">
                <Field label="Skills">
                  <Textarea
                    rows={3}
                    value={skillsText}
                    onChange={(event) => setSkillsText(event.target.value)}
                    placeholder="Python, FastAPI, AWS, Kubernetes, LangChain"
                  />
                </Field>
                <div className="grid gap-4 md:grid-cols-2">
                  <Field label="Certifications">
                    <Input
                      value={certificationsText}
                      onChange={(event) => setCertificationsText(event.target.value)}
                      placeholder="AWS SAA, Azure AI Engineer"
                    />
                  </Field>
                  <Field label="Languages">
                    <Input
                      value={languagesText}
                      onChange={(event) => setLanguagesText(event.target.value)}
                      placeholder="English, Hindi, Spanish"
                    />
                  </Field>
                </div>
                <Field label="Achievement bullets">
                  <Textarea
                    rows={5}
                    value={achievementsText}
                    onChange={(event) => setAchievementsText(event.target.value)}
                    placeholder="One metric-backed achievement per line."
                  />
                </Field>
              </CardContent>
            </Card>

            <ModuleList
              title="Experience"
              description="Reusable work-history modules for forms, letters, and recruiter emails."
              icon={<BriefcaseBusiness className="h-5 w-5" aria-hidden="true" />}
              addLabel="Add Experience"
              onAdd={() => patchProfile({ experience_json: [...profileForm.experience_json, { ...emptyExperience }] })}
            >
              {profileForm.experience_json.length ? (
                profileForm.experience_json.map((experience, index) => (
                  <ExperienceEditor
                    key={`experience-${index}`}
                    experience={experience}
                    index={index}
                    onChange={(next) => updateArrayItem(profileForm.experience_json, index, next, (items) => patchProfile({ experience_json: items }))}
                    onRemove={() => removeArrayItem(profileForm.experience_json, index, (items) => patchProfile({ experience_json: items }))}
                  />
                ))
              ) : (
                <EmptyModule label="No experience modules yet." />
              )}
            </ModuleList>

            <ModuleList
              title="Projects"
              description="Concrete shipped work the LLM can cite in cover letters and cold emails."
              icon={<Sparkles className="h-5 w-5" aria-hidden="true" />}
              addLabel="Add Project"
              onAdd={() => patchProfile({ projects_json: [...profileForm.projects_json, { ...emptyProject }] })}
            >
              {profileForm.projects_json.length ? (
                profileForm.projects_json.map((project, index) => (
                  <ProjectEditor
                    key={`project-${index}`}
                    project={project}
                    index={index}
                    onChange={(next) => updateArrayItem(profileForm.projects_json, index, next, (items) => patchProfile({ projects_json: items }))}
                    onRemove={() => removeArrayItem(profileForm.projects_json, index, (items) => patchProfile({ projects_json: items }))}
                  />
                ))
              ) : (
                <EmptyModule label="No project modules yet." />
              )}
            </ModuleList>

            <ModuleList
              title="Education"
              description="School, degree, and graduation details for application forms."
              icon={<GraduationCap className="h-5 w-5" aria-hidden="true" />}
              addLabel="Add Education"
              onAdd={() =>
                patchProfile({
                  education_json: [...profileForm.education_json, { school: "", degree: "", year: "" }],
                })
              }
            >
              {profileForm.education_json.length ? (
                profileForm.education_json.map((education, index) => (
                  <div key={`education-${index}`} className="grid gap-3 rounded-md border p-4 md:grid-cols-[1fr_1fr_160px_auto]">
                    <Input
                      placeholder="School"
                      value={education.school}
                      onChange={(event) =>
                        updateArrayItem(
                          profileForm.education_json,
                          index,
                          { ...education, school: event.target.value },
                          (items) => patchProfile({ education_json: items }),
                        )
                      }
                    />
                    <Input
                      placeholder="Degree"
                      value={education.degree}
                      onChange={(event) =>
                        updateArrayItem(
                          profileForm.education_json,
                          index,
                          { ...education, degree: event.target.value },
                          (items) => patchProfile({ education_json: items }),
                        )
                      }
                    />
                    <Input
                      placeholder="Year"
                      value={education.year}
                      onChange={(event) =>
                        updateArrayItem(
                          profileForm.education_json,
                          index,
                          { ...education, year: event.target.value },
                          (items) => patchProfile({ education_json: items }),
                        )
                      }
                    />
                    <IconButton label="Remove education" onClick={() => removeArrayItem(profileForm.education_json, index, (items) => patchProfile({ education_json: items }))} />
                  </div>
                ))
              ) : (
                <EmptyModule label="No education modules yet." />
              )}
            </ModuleList>

            <Card>
              <CardHeader>
                <CardTitle>Voluntary Self Identification</CardTitle>
                <CardDescription>Optional EEO defaults for forms that ask these questions.</CardDescription>
              </CardHeader>
              <CardContent className="grid gap-4 md:grid-cols-2">
                <Field label="Gender">
                  <Select value={profileForm.eeo_json.gender ?? ""} onChange={(event) => patchEeo({ gender: event.target.value })}>
                    <option value="">Prefer not to answer</option>
                    <option value="Female">Female</option>
                    <option value="Male">Male</option>
                    <option value="Non-binary">Non-binary</option>
                    <option value="Prefer not to answer">Prefer not to answer</option>
                  </Select>
                </Field>
                <Field label="Race / ethnicity">
                  <Input
                    value={profileForm.eeo_json.race_ethnicity ?? ""}
                    onChange={(event) => patchEeo({ race_ethnicity: event.target.value })}
                    placeholder="Prefer not to answer"
                  />
                </Field>
                <Field label="Veteran status">
                  <Select
                    value={profileForm.eeo_json.veteran_status ?? ""}
                    onChange={(event) => patchEeo({ veteran_status: event.target.value })}
                  >
                    <option value="">Prefer not to answer</option>
                    <option value="Not a protected veteran">Not a protected veteran</option>
                    <option value="Protected veteran">Protected veteran</option>
                    <option value="Prefer not to answer">Prefer not to answer</option>
                  </Select>
                </Field>
                <Field label="Disability status">
                  <Select
                    value={profileForm.eeo_json.disability_status ?? ""}
                    onChange={(event) => patchEeo({ disability_status: event.target.value })}
                  >
                    <option value="">Prefer not to answer</option>
                    <option value="No, I do not have a disability">No, I do not have a disability</option>
                    <option value="Yes, I have a disability">Yes, I have a disability</option>
                    <option value="Prefer not to answer">Prefer not to answer</option>
                  </Select>
                </Field>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="keys">
          <Card>
            <CardHeader>
              <CardTitle>API Keys</CardTitle>
              <CardDescription>Masked values mirror the backend `.env` names.</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4 md:grid-cols-2">
              {[
                "APOLLO_API_KEY",
                "INDEED_PUBLISHER_ID",
                "JOBRIGHT_SESSION_TOKEN",
                "SMTP_HOST",
                "SMTP_PORT",
                "SMTP_USER",
                "SMTP_PASSWORD",
                "LINKEDIN_EMAIL",
                "LINKEDIN_PASSWORD",
              ].map((key) => (
                <Input key={key} type={key.includes("PASSWORD") || key.includes("TOKEN") ? "password" : "text"} placeholder={key} />
              ))}
              <div className="md:col-span-2">
                <Button type="button" onClick={() => setMessage("Runtime secrets are loaded from .env by the FastAPI backend.")}>
                  Save Key View
                </Button>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="scraper">
          <Card>
            <CardHeader>
              <CardTitle>Scraper</CardTitle>
              <CardDescription>Local scheduler preferences for scraping and applying.</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-5">
              <label className="grid gap-2">
                <span className="text-sm font-medium">Scrape interval: {scrapeInterval} hours</span>
                <Slider
                  min={1}
                  max={24}
                  value={scrapeInterval}
                  onChange={(event) => setScrapeInterval(Number(event.target.value))}
                />
              </label>
              <label className="grid gap-2">
                <span className="text-sm font-medium">Daily apply limit: {dailyLimit}</span>
                <Slider
                  min={1}
                  max={50}
                  value={dailyLimit}
                  onChange={(event) => setDailyLimit(Number(event.target.value))}
                />
              </label>
              <Textarea
                rows={4}
                value={queries}
                onChange={(event) => setQueries(event.target.value)}
                placeholder="Job query keywords"
              />
              <Textarea
                rows={4}
                value={blacklist}
                onChange={(event) => setBlacklist(event.target.value)}
                placeholder="Blacklisted companies, comma separated"
              />
              <Button type="button" onClick={() => setMessage("Scraper settings saved in this browser session.")}>
                Save Scraper Settings
              </Button>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="notifications">
          <Card>
            <CardHeader>
              <CardTitle>Notifications</CardTitle>
              <CardDescription>Email and desktop notifications will appear here in a later phase.</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="rounded-md border border-dashed bg-slate-50 px-5 py-10 text-sm text-muted-foreground">
                Notification routing is not enabled yet.
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </>
  );
}

function Field({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <label className={`grid gap-2 ${className ?? ""}`}>
      <span className="text-sm font-medium text-slate-800">{label}</span>
      {children}
    </label>
  );
}

function CheckboxRow({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <label className="flex min-h-10 items-center gap-2 rounded-md border bg-card px-3 py-2 text-sm">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      {label}
    </label>
  );
}

function ModuleList({
  title,
  description,
  icon,
  addLabel,
  onAdd,
  children,
}: {
  title: string;
  description: string;
  icon: React.ReactNode;
  addLabel: string;
  onAdd: () => void;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2">
              {icon}
              {title}
            </CardTitle>
            <CardDescription>{description}</CardDescription>
          </div>
          <Button type="button" size="sm" variant="outline" onClick={onAdd}>
            <Plus className="h-4 w-4" aria-hidden="true" />
            {addLabel}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="grid gap-4">{children}</CardContent>
    </Card>
  );
}

function ExperienceEditor({
  experience,
  index,
  onChange,
  onRemove,
}: {
  experience: ExperienceItem;
  index: number;
  onChange: (experience: ExperienceItem) => void;
  onRemove: () => void;
}) {
  return (
    <div className="grid gap-4 rounded-md border p-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-semibold">Experience {index + 1}</p>
        <IconButton label="Remove experience" onClick={onRemove} />
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="Title">
          <Input value={experience.title} onChange={(event) => onChange({ ...experience, title: event.target.value })} />
        </Field>
        <Field label="Company">
          <Input value={experience.company} onChange={(event) => onChange({ ...experience, company: event.target.value })} />
        </Field>
        <Field label="Location">
          <Input value={experience.location ?? ""} onChange={(event) => onChange({ ...experience, location: event.target.value })} />
        </Field>
        <Field label="Technologies">
          <Input
            value={(experience.technologies ?? []).join(", ")}
            onChange={(event) => onChange({ ...experience, technologies: splitCsv(event.target.value) })}
            placeholder="Python, AWS, React"
          />
        </Field>
        <Field label="Start">
          <Input value={experience.start_date ?? ""} onChange={(event) => onChange({ ...experience, start_date: event.target.value })} />
        </Field>
        <Field label="End">
          <Input value={experience.end_date ?? ""} onChange={(event) => onChange({ ...experience, end_date: event.target.value })} />
        </Field>
      </div>
      <Field label="Impact bullets">
        <Textarea
          rows={4}
          value={(experience.highlights ?? []).join("\n")}
          onChange={(event) => onChange({ ...experience, highlights: splitLines(event.target.value) })}
          placeholder="One bullet per line, ideally with metrics."
        />
      </Field>
    </div>
  );
}

function ProjectEditor({
  project,
  index,
  onChange,
  onRemove,
}: {
  project: ProjectItem;
  index: number;
  onChange: (project: ProjectItem) => void;
  onRemove: () => void;
}) {
  return (
    <div className="grid gap-4 rounded-md border p-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-semibold">Project {index + 1}</p>
        <IconButton label="Remove project" onClick={onRemove} />
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="Name">
          <Input value={project.name} onChange={(event) => onChange({ ...project, name: event.target.value })} />
        </Field>
        <Field label="URL">
          <Input value={project.url ?? ""} onChange={(event) => onChange({ ...project, url: event.target.value })} />
        </Field>
      </div>
      <Field label="Description">
        <Textarea rows={3} value={project.description} onChange={(event) => onChange({ ...project, description: event.target.value })} />
      </Field>
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="Impact">
          <Input value={project.impact ?? ""} onChange={(event) => onChange({ ...project, impact: event.target.value })} />
        </Field>
        <Field label="Technologies">
          <Input
            value={(project.technologies ?? []).join(", ")}
            onChange={(event) => onChange({ ...project, technologies: splitCsv(event.target.value) })}
            placeholder="FastAPI, ChromaDB, Ollama"
          />
        </Field>
      </div>
    </div>
  );
}

function EmptyModule({ label }: { label: string }) {
  return <div className="rounded-md border border-dashed bg-slate-50 px-4 py-6 text-sm text-muted-foreground">{label}</div>;
}

function IconButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <Button type="button" size="sm" variant="outline" onClick={onClick} aria-label={label}>
      <Minus className="h-4 w-4" aria-hidden="true" />
    </Button>
  );
}

function updateArrayItem<T>(items: T[], index: number, next: T, commit: (items: T[]) => void) {
  commit(items.map((item, itemIndex) => (itemIndex === index ? next : item)));
}

function removeArrayItem<T>(items: T[], index: number, commit: (items: T[]) => void) {
  commit(items.filter((_, itemIndex) => itemIndex !== index));
}

function buildProfilePayload({
  form,
  skillsText,
  targetRolesText,
  preferredLocationsText,
  achievementsText,
  certificationsText,
  languagesText,
}: {
  form: ProfileInput;
  skillsText: string;
  targetRolesText: string;
  preferredLocationsText: string;
  achievementsText: string;
  certificationsText: string;
  languagesText: string;
}): ProfileInput {
  return {
    ...form,
    skills_json: splitCsv(skillsText),
    target_roles_json: splitCsv(targetRolesText),
    preferred_locations_json: splitCsv(preferredLocationsText),
    achievements_json: splitLines(achievementsText),
    certifications_json: splitCsv(certificationsText),
    languages_json: splitCsv(languagesText),
    education_json: form.education_json.filter((item) => item.school || item.degree || item.year),
    experience_json: form.experience_json
      .map((item) => ({
        ...item,
        highlights: item.highlights.filter(Boolean),
        technologies: item.technologies.filter(Boolean),
      }))
      .filter((item) => item.company || item.title || item.highlights.length),
    projects_json: form.projects_json
      .map((item) => ({
        ...item,
        technologies: item.technologies.filter(Boolean),
      }))
      .filter((item) => item.name || item.description || item.impact),
    application_preferences_json: {
      ...form.application_preferences_json,
      employment_types: form.application_preferences_json.employment_types.filter(Boolean),
    },
    eeo_json: form.eeo_json,
  };
}

function splitLines(value: string) {
  return value
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}

function boolToSelect(value?: boolean | null) {
  if (value === true) return "true";
  if (value === false) return "false";
  return "";
}

function selectToBool(value: string) {
  if (value === "true") return true;
  if (value === "false") return false;
  return null;
}

function profileCompletion(form: ProfileInput, skillsText: string, targetRolesText: string) {
  const checks = [
    form.full_name,
    form.email,
    form.phone,
    form.location,
    form.linkedin_url,
    form.github_url || form.portfolio_url,
    form.work_authorization,
    form.summary,
    skillsText,
    targetRolesText,
    form.experience_json.length > 0,
    form.projects_json.length > 0 || form.achievements_json.length > 0,
    form.education_json.length > 0,
    form.application_preferences_json.remote_preference,
    form.salary_min || form.salary_max,
  ];
  const complete = checks.filter(Boolean).length;
  return Math.round((complete / checks.length) * 100);
}
