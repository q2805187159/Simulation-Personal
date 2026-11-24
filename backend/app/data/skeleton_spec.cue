@experiment(explicitopen)

package skeleton

import (
	"math"
	"strings"
)

// The normalized output of domain.compile_spec. Catalog membership and
// chronological rules remain domain-level checks because they depend on the
// versioned runtime catalogs.

#CatalogCode: string & =~"^[0-9]{2}$" & strings.MaxRunes(2)
#EthnicityCode: string & =~"^([0-9]{2}|(cn|hk|mo|tw)-[a-z0-9]+(-[a-z0-9]+)*)$" & strings.MaxRunes(48)
#ISODate:     string & =~"^[0-9]{4}-[0-9]{2}-[0-9]{2}$" & strings.MaxRunes(10)
#Year:        number & math.MultipleOf(1) & >=1880 & <=2100

// GenerationOptions enforces the exact signed 64-bit upper bound on input.
#Seed:       number & math.MultipleOf(1) & >=0
#Entry:      "birth_cohort" | "living_at_as_of"
#Household:  "individual" | "family"
#Detail:     "standard" | "rich"
#LifeStatus: "living" | "deceased"
#RuleID:     string & =~"^[A-Z0-9]+(-[A-Z0-9]+)*$" & !~"[\\r\\n]"
#Text:       string & strings.MinRunes(1)

#BirthYearSampling: {source!: "provided"} | {
	source!: "sampled"
	range!: [#Year, #Year]
}

#Probability: number & >=0 & <=1

#Population: {
	model_version!:                    "synthetic-population/1"
	synthetic!:                        true
	frame!:                            #Entry
	conditioning!:                     "unconditional" | "survived_through_as_of" | "died_by_as_of"
	birth_year!:                       #BirthYearSampling
	birth_year_selection_probability!: #Probability
	frame_birth_year_count!:           number & math.MultipleOf(1) & >=1
	frame_population_size!:            null
	frame_inclusion_probability!:      #Probability
	frame_log_inclusion_probability!:  number & <=0
	cohort_survival_probability!:      #Probability
	risk_class!:                       "lower" | "middle" | "higher"
	risk_class_selection_probability!: #Probability
	right_censored!:                   bool
	analysis_weight!:                  null
}

#HistorySelection: {
	id!:     #RuleID
	label!:  #Text
	reason!: #Text
}

#SkeletonSpec: {
	region!:          #CatalogCode
	region_label!:    #Text
	ethnicity!:       #EthnicityCode
	ethnicity_label!: #Text
	birth_year!:      #Year
	as_of!:           #ISODate
	entry!:           #Entry
	household!:       #Household
	detail!:          #Detail
	seed!:            #Seed
	life_status!:     #LifeStatus
	death_date?:      #ISODate | null
	population?:      #Population
	population_selection?: {
		version!:               #Text
		frame_id!:              #Text
		cell_count!:            int & >0
		eligible_population!:   int & >0
		frame_population!:      int & >0
		selection_probability!: #Probability
		filters!: {
			region!:    #CatalogCode | null
			ethnicity!: #EthnicityCode | null
		}
	}
	sampling!: {
		birth_year!: #BirthYearSampling
		household!: {source!: "provided" | "sampled"}
	}
	history_selection!: [...#HistorySelection]
	themes!: [...#RuleID]
}
